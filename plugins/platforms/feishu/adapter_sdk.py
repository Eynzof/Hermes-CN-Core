"""Lazy SDK model bindings for the Feishu adapter."""

def _lark_bindings() -> dict:
    """Import lark_oapi + the model classes the adapter needs.

    Returns a ``{name: value}`` dict suitable for ``ensure_and_bind``. Costs
    ~2.5s the first time it runs (the SDK eagerly imports its whole ``api``
    package); afterwards all names are cached in ``sys.modules`` and this
    returns in microseconds.
    """
    import lark_oapi as lark
    from lark_oapi.api.application.v6 import GetApplicationRequest
    from lark_oapi.api.im.v1 import (
        CreateFileRequest,
        CreateFileRequestBody,
        CreateImageRequest,
        CreateImageRequestBody,
        CreateMessageRequest,
        CreateMessageRequestBody,
        DeleteMessageRequest,
        GetChatRequest,
        GetMessageRequest,
        GetMessageResourceRequest,
        P2ImMessageMessageReadV1,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
        UpdateMessageRequest,
        UpdateMessageRequestBody,
    )
    from lark_oapi.core import AccessTokenType, HttpMethod
    from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN
    from lark_oapi.core.model import BaseRequest
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        CallBackCard,
        P2CardActionTriggerResponse,
    )
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
    from lark_oapi.ws import Client as FeishuWSClient

    return {
        "lark": lark,
        "GetApplicationRequest": GetApplicationRequest,
        "CreateFileRequest": CreateFileRequest,
        "CreateFileRequestBody": CreateFileRequestBody,
        "CreateImageRequest": CreateImageRequest,
        "CreateImageRequestBody": CreateImageRequestBody,
        "CreateMessageRequest": CreateMessageRequest,
        "CreateMessageRequestBody": CreateMessageRequestBody,
        "DeleteMessageRequest": DeleteMessageRequest,
        "GetChatRequest": GetChatRequest,
        "GetMessageRequest": GetMessageRequest,
        "GetMessageResourceRequest": GetMessageResourceRequest,
        "P2ImMessageMessageReadV1": P2ImMessageMessageReadV1,
        "ReplyMessageRequest": ReplyMessageRequest,
        "ReplyMessageRequestBody": ReplyMessageRequestBody,
        "UpdateMessageRequest": UpdateMessageRequest,
        "UpdateMessageRequestBody": UpdateMessageRequestBody,
        "AccessTokenType": AccessTokenType,
        "HttpMethod": HttpMethod,
        "FEISHU_DOMAIN": FEISHU_DOMAIN,
        "LARK_DOMAIN": LARK_DOMAIN,
        "BaseRequest": BaseRequest,
        "CallBackCard": CallBackCard,
        "P2CardActionTriggerResponse": P2CardActionTriggerResponse,
        "EventDispatcherHandler": EventDispatcherHandler,
        "FeishuWSClient": FeishuWSClient,
        "FEISHU_AVAILABLE": True,
    }
