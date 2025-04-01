import hashlib

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message


def sha256_gprc_msg(message: Message) -> str:
    """
    Creates the sha256sum of the grpc message.
    """
    return hashlib.sha256(message.SerializeToString(deterministic=True)).hexdigest()


def convert_msg_to_dict(msg: Message, field: str) -> dict | None:
    if not msg.HasField(field):
        return None
    return MessageToDict(getattr(msg, field))
