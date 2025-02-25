#!/bin/bash 

# This script compiles the proto files for lnd

REPOBASE="https://raw.githubusercontent.com/lightningnetwork/lnd/refs/heads"
BRANCH="0-18-5-branch"
PROTOC="python -m grpc_tools.protoc --proto_path=. --mypy_out=. --python_out=. --grpc_python_out=." 


function process_proto {
    REPOPATH=$1
    PROTO=$2
    wget "$REPOBASE/$BRANCH/$REPOPATH/$PROTO" -O $PROTO -q

    # pre process before compilation. Actually only needed for walletkit.proto
    sed -i "s#signrpc/signer.proto#signer.proto#g" $PROTO

    # compile the proto
    $PROTOC $PROTO 
}

function post_process {
    PROTO="$1"
    sed -i "s#import ${PROTO}_pb2 as ${PROTO}__pb2#from . import ${PROTO}_pb2 as ${PROTO}__pb2#g" *.py
}

process_proto lnrpc lightning.proto
process_proto lnrpc/routerrpc router.proto
process_proto lnrpc/signrpc signer.proto
process_proto lnrpc/walletrpc walletkit.proto

post_process lightning
post_process router
post_process signer
post_process walletkit

mv *.py *.pyi ../grpc_generated/