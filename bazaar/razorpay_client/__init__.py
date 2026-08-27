from bazaar.razorpay_client.base import PaymentsClient, WebhookEvent, verify_webhook_signature
from bazaar.razorpay_client.fake import FakeRazorpay


def get_payments_client(kind: str | None = None) -> PaymentsClient:
    """Select the payments backend from settings (``fake`` by default, ``razorpay`` with test keys)."""
    from bazaar.settings import get_settings

    s = get_settings()
    kind = kind or s.bazaar_razorpay
    if kind == "razorpay":
        from bazaar.razorpay_client.real import RazorpayClient

        return RazorpayClient(s.razorpay_key_id, s.razorpay_key_secret)
    return FakeRazorpay.shared()


__all__ = ["PaymentsClient", "FakeRazorpay", "WebhookEvent", "get_payments_client", "verify_webhook_signature"]
