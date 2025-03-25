import hashlib

from google.protobuf.message import Message


def sha256_gprc_msg(message: Message) -> str:
    """
    Creates the sha256sum of the grpc message.
    """
    return hashlib.sha256(message.SerializeToString(deterministic=True)).hexdigest()
