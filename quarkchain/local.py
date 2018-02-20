from quarkchain.core import Serializable, uint8, uint32, PreprendedSizeListSerializer, PreprendedSizeBytesSerializer
from quarkchain.core import Address, RootBlock, MinorBlock, Transaction
from quarkchain.protocol import Connection, ConnectionState
import asyncio
import statistics
import time
import json


class GetBlockTemplateRequest(Serializable):
    FIELDS = (
        ("address", Address),
        ("includeRoot", uint8),
        ("shardMaskList", PreprendedSizeListSerializer(
            4, uint32)),  # TODO create shard mask object
    )

    def __init__(self, address, includeRoot=True, shardMaskList=None):
        shardMaskList = [] if shardMaskList is None else shardMaskList
        self.address = address
        self.includeRoot = includeRoot
        self.shardMaskList = shardMaskList


class GetBlockTemplateResponse(Serializable):
    FIELDS = (
        ("isRootBlock", uint8),
        ("blockData", PreprendedSizeBytesSerializer(4))
    )

    def __init__(self, isRootBlock, blockData):
        self.isRootBlock = isRootBlock
        self.blockData = blockData


class SubmitNewBlockRequest(Serializable):
    FIELDS = (
        ("isRootBlock", uint8),
        ("blockData", PreprendedSizeBytesSerializer(4))
    )

    def __init__(self, isRootBlock, blockData):
        self.isRootBlock = isRootBlock
        self.blockData = blockData


class SubmitNewBlockResponse(Serializable):
    FIELDS = (
        ("resultCode", uint8),
        ("resultMessage", PreprendedSizeBytesSerializer(4))
    )

    def __init__(self, resultCode, resultMessage=bytes(0)):
        self.resultCode = resultCode
        self.resultMessage = resultMessage


class NewTransaction(Serializable):
    FIELDS = (
        ("shardId", uint32),
        ("transaction", Transaction),
    )

    def __init__(self, shardId, transaction):
        """ Negative shardId indicates unknown shard (not support yet)
        """
        self.shardId = shardId
        self.transaction = transaction


class AddNewTransactionListRequest(Serializable):
    FIELDS = (
        ("txList", PreprendedSizeListSerializer(4, NewTransaction)),
    )

    def __init__(self, txList):
        self.txList = txList


class AddNewTransactionListResponse(Serializable):
    FIELDS = (
        ("numTxAdded", uint32)
    )

    def __init__(self, numTxAdded):
        self.numTxAdded = numTxAdded


class JsonRpcRequest(Serializable):
    FIELDS = (
        ("jrpcRequest", PreprendedSizeBytesSerializer(4)),
    )

    def __init__(self, jrpcRequest):
        self.jrpcRequest = jrpcRequest


class JsonRpcResponse(Serializable):
    FIELDS = (
        ("jrpcResponse", PreprendedSizeBytesSerializer(4)),
    )

    def __init__(self, jrpcResponse):
        self.jrpcResponse = jrpcResponse


class LocalCommandOp:
    GET_BLOCK_TEMPLATE_REQUEST = 0
    GET_BLOCK_TEMPLATE_RESPONSE = 1
    SUBMIT_NEW_BLOCK_REQUEST = 2
    SUBMIT_NEW_BLOCK_RESPONSE = 3
    ADD_NEW_TRANSACTION_LIST_REQUEST = 4
    ADD_NEW_TRANSACTION_LIST_RESPONSE = 5
    JSON_RPC_REQUEST = 6
    JSON_RPC_RESPONSE = 7


OP_SER_MAP = {
    LocalCommandOp.GET_BLOCK_TEMPLATE_REQUEST: GetBlockTemplateRequest,
    LocalCommandOp.GET_BLOCK_TEMPLATE_RESPONSE: GetBlockTemplateResponse,
    LocalCommandOp.SUBMIT_NEW_BLOCK_REQUEST: SubmitNewBlockRequest,
    LocalCommandOp.SUBMIT_NEW_BLOCK_RESPONSE: SubmitNewBlockResponse,
    LocalCommandOp.ADD_NEW_TRANSACTION_LIST_REQUEST: AddNewTransactionListRequest,
    LocalCommandOp.ADD_NEW_TRANSACTION_LIST_RESPONSE: AddNewTransactionListResponse,
    LocalCommandOp.JSON_RPC_REQUEST: JsonRpcRequest,
    LocalCommandOp.JSON_RPC_RESPONSE: JsonRpcResponse,
}


class LocalServer(Connection):

    def __init__(self, env, reader, writer, network):
        super().__init__(env, reader, writer, OP_SER_MAP, dict(), OP_RPC_MAP)
        self.network = network

    async def start(self):
        self.state = ConnectionState.ACTIVE
        asyncio.ensure_future(self.loopForever())

    async def handleGetBlockTemplateRequest(self, request):
        isRootBlock, block = self.network.qcState.findBestBlockToMine()

        if isRootBlock is None:
            response = GetBlockTemplateResponse(0, bytes(0))
        elif isRootBlock:
            response = GetBlockTemplateResponse(1, block.serialize())
            print("obtained root block to mine, height {}, diff {}".format(
                block.header.height, block.header.difficulty))
        else:
            response = GetBlockTemplateResponse(0, block.serialize())
            print("obtained minor block to mine, shard {}, height {}, diff {}".format(
                block.header.branch.getShardId(), block.header.height, block.header.difficulty))
        return response

    async def handleSubmitNewBlockRequest(self, request):
        if request.isRootBlock:
            try:
                rBlock = RootBlock.deserialize(request.blockData)
            except Exception as e:
                return SubmitNewBlockResponse(1, bytes("{}".format(e), "ascii"))
            msg = self.network.qcState.appendRootBlock(rBlock)
            if msg is None:
                return SubmitNewBlockResponse(resultCode=0)
            else:
                return SubmitNewBlockResponse(
                    resultCode=1, resultMessage=bytes(msg, "ascii"))
        else:
            try:
                mBlock = MinorBlock.deserialize(request.blockData)
            except Exception as e:
                return SubmitNewBlockResponse(
                    resultCode=1, resultMessage=bytes("{}".format(e), "ascii"))

            msg = self.network.qcState.appendMinorBlock(mBlock)
            if msg is None:
                return SubmitNewBlockResponse(resultCode=0)
            else:
                return SubmitNewBlockResponse(
                    resultCode=1, resultMessage=bytes(msg, "ascii"))

    async def handleAddNewTransactionListRequest(self, request):
        for newTx in request.txList:
            self.network.qcState.addTransactionToQueue()
        return AddNewTransactionListResponse(len(request.txList))

    def closeWithError(self, error):
        print("Closing with error {}".format(error))
        return super().closeWithError(error)

    def countMinorBlockHeaderStatsIn(self, sec, func):
        qcState = self.network.qcState
        now = time.time()
        metric = 0
        for shardId in range(qcState.getShardSize()):
            header = qcState.getMinorBlockTip(shardId)
            while header.createTime >= now - sec:
                metric += func(header)
                if header.height == 0:
                    break
                header = qcState.getMinorBlockHeaderByHeight(shardId, header.height - 1)
        return metric

    def countMinorBlockStatsIn(self, sec, func):
        qcState = self.network.qcState
        now = time.time()
        metric = 0
        for shardId in range(qcState.getShardSize()):
            header = qcState.getMinorBlockTip(shardId)
            self.env.db.get
            while header.createTime >= now - sec:
                block = self.env.db.getMinorBlockByHash(header.getHash())
                metric += func(block)
                if header.height == 0:
                    break
                header = qcState.getMinorBlockHeaderByHeight(shardId, header.height - 1)
        return metric

    async def jrpcGetStats(self, params):
        qcState = self.network.qcState
        resp = {
            "shardSize": qcState.getShardSize(),
            "rootHeight": qcState.getRootBlockTip().height,
            "rootDifficulty": qcState.getRootBlockTip().difficulty,
            "avgMinorHeight": statistics.mean(
                [qcState.getMinorBlockTip(shardId).height for shardId in range(qcState.getShardSize())]),
            "avgMinorDifficulty": statistics.mean(
                [qcState.getMinorBlockTip(shardId).difficulty for shardId in range(qcState.getShardSize())]),
            "minorBlocksIn60s": self.countMinorBlockHeaderStatsIn(60, lambda h: 1),
            "minorBlocksIn300s": self.countMinorBlockHeaderStatsIn(300, lambda h: 1),
            "transactionsIn60s": self.countMinorBlockStatsIn(60, lambda b: len(b.txList)),
            "transactionsIn300s": self.countMinorBlockStatsIn(300, lambda b: len(b.txList)),
        }
        return resp

    def jrpcError(self, errorCode, jrpcId=None):
        response = {
            "jsonrpc": "2.0",
            "error": {"code": errorCode},
        }
        if jrpcId is not None:
            response["id"] = jrpcId
        return JsonRpcResponse(json.dumps(response).encode())

    async def handleJsonRpcRequest(self, request):
        # TODO: Better jrpc handling
        try:
            jrpcRequest = json.loads(request.jrpcRequest.decode("utf8"))
        except Exception as e:
            return self.jrpcError(-32700)

        if "jsonrpc" not in jrpcRequest or jrpcRequest["jsonrpc"] != "2.0":
            return self.jrpcError(-32600)

        # Ignore id at the monent

        if "method" not in jrpcRequest:
            return self.jrpcError(-32600)

        method = jrpcRequest["method"]
        if method not in JRPC_MAP:
            return self.jrpcError(-32601)

        params = None if "params" not in jrpcRequest else jrpcRequest["params"]

        try:
            jrpcResponse = await JRPC_MAP[method](self, params)
            return JsonRpcResponse(json.dumps(jrpcResponse).encode())
        except Exception as e:
            return self.jrpcError(-32603)


OP_RPC_MAP = {
    LocalCommandOp.GET_BLOCK_TEMPLATE_REQUEST:
        (LocalCommandOp.GET_BLOCK_TEMPLATE_RESPONSE,
         LocalServer.handleGetBlockTemplateRequest),
    LocalCommandOp.SUBMIT_NEW_BLOCK_REQUEST:
        (LocalCommandOp.SUBMIT_NEW_BLOCK_RESPONSE,
         LocalServer.handleSubmitNewBlockRequest),
    LocalCommandOp.ADD_NEW_TRANSACTION_LIST_REQUEST:
        (LocalCommandOp.ADD_NEW_TRANSACTION_LIST_RESPONSE,
         LocalServer.handleAddNewTransactionListRequest),
    LocalCommandOp.JSON_RPC_REQUEST:
        (LocalCommandOp.JSON_RPC_RESPONSE,
         LocalServer.handleJsonRpcRequest)
}

JRPC_MAP = {
    "getStats": LocalServer.jrpcGetStats,
}