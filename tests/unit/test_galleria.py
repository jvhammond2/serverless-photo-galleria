"""
Targeted unit tests for critical audit fixes — no AWS credentials needed.
All checks are static (source inspection) or use minimal mocking.
"""
import os, sys, unittest

sys.path.insert(0, "/sessions/elegant-tender-carson/mnt/serverless-photo-galleria/src")
os.environ.update({
    "METADATA_TABLE": "t", "ORDERS_TABLE": "t", "PROFILE_TABLE": "t",
    "ORIGINALS_BUCKET": "t", "THUMBS_BUCKET": "t", "PREVIEWS_BUCKET": "t",
    "AWS_DEFAULT_REGION": "us-east-1",
})

BASE = "/sessions/elegant-tender-carson/mnt/serverless-photo-galleria"

def src(rel):
    return open(f"{BASE}/{rel}").read()


class TestC1_IndexResponseShape(unittest.TestCase):
    def test_handles_data_photos_wrapper(self):
        s = src("index.html")
        self.assertIn("data.photos||data||[]", s,
            "index.html must unwrap {photos:[]} response shape")

class TestC4_EarningsStatusFilter(unittest.TestCase):
    def test_filters_on_paid_not_completed(self):
        s = src("src/earnings/app.py")
        self.assertIn('"paid"', s, "earnings must filter on status='paid'")
        self.assertNotIn('"completed"', s, "earnings must NOT filter on status='completed'")

class TestC5_OrdersHaveUserId(unittest.TestCase):
    def test_get_download_writes_userid(self):
        s = src("src/get_download/app.py")
        self.assertIn('"userId"', s, "get_download must write userId to orders")
        self.assertIn('"paymentIntentId"', s, "get_download must write paymentIntentId")

    def test_stripe_webhook_writes_userid(self):
        s = src("src/stripe_webhook/app.py")
        self.assertIn('"userId"', s, "stripe_webhook must write userId to orders")
        self.assertIn('"paymentIntentId"', s, "stripe_webhook must write paymentIntentId")

    def test_checkout_session_includes_userid_in_metadata(self):
        s = src("src/create_checkout_session/app.py")
        self.assertIn('"userId"', s, "checkout session must pass userId in Stripe metadata")
        self.assertIn("customer_sub", s, "checkout session must extract customer sub from Cognito claims")

class TestC6_MultiItemWebhook(unittest.TestCase):
    def test_webhook_handles_multi_item_cart(self):
        s = src("src/stripe_webhook/app.py")
        self.assertIn("is_multi", s, "webhook must handle multi=1 cart sessions")
        self.assertIn("photo_ids_raw", s, "webhook must process photoIds metadata field")

class TestC7_NoGlobalMutation(unittest.TestCase):
    def test_processing_no_global_watermark(self):
        s = src("src/processing/app.py")
        self.assertNotIn("global WATERMARK_TEXT", s,
            "processing handler must not mutate module-level WATERMARK_TEXT")
        self.assertIn("watermark_text = _get_watermark_text", s,
            "watermark text must be resolved into a local variable")

class TestC8_TaggingWritesFields(unittest.TestCase):
    def test_tagging_writes_photographer_id(self):
        s = src("src/tagging/app.py")
        self.assertIn('"photographerId"', s, "tagging must write photographerId to DynamoDB")
        self.assertIn('"fileName"', s, "tagging must write fileName to DynamoDB")
        self.assertIn('"status"', s, "tagging must write status field to DynamoDB")

class TestW8_ConsentNoAuditOnRead(unittest.TestCase):
    def test_no_audit_write_on_get(self):
        s = src("src/consent/app.py")
        audit_read_lines = [l.strip() for l in s.splitlines()
                            if "_write_audit" in l and "read" in l
                            and not l.strip().startswith("#")]
        self.assertEqual([], audit_read_lines,
            "consent GET must not call _write_audit for reads")

class TestW6_StripeWebhookComplete(unittest.TestCase):
    def test_webhook_syntax_valid(self):
        import py_compile, tempfile, shutil
        src_path = f"{BASE}/src/stripe_webhook/app.py"
        try:
            py_compile.compile(src_path, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"stripe_webhook/app.py has a syntax error: {e}")

    def test_get_download_syntax_valid(self):
        import py_compile
        src_path = f"{BASE}/src/get_download/app.py"
        try:
            py_compile.compile(src_path, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"get_download/app.py has a syntax error: {e}")

    def test_earnings_syntax_valid(self):
        import py_compile
        src_path = f"{BASE}/src/earnings/app.py"
        try:
            py_compile.compile(src_path, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"earnings/app.py has a syntax error: {e}")

    def test_tagging_syntax_valid(self):
        import py_compile
        src_path = f"{BASE}/src/tagging/app.py"
        try:
            py_compile.compile(src_path, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"tagging/app.py has a syntax error: {e}")

    def test_processing_syntax_valid(self):
        import py_compile
        src_path = f"{BASE}/src/processing/app.py"
        try:
            py_compile.compile(src_path, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"processing/app.py has a syntax error: {e}")

    def test_consent_syntax_valid(self):
        import py_compile
        src_path = f"{BASE}/src/consent/app.py"
        try:
            py_compile.compile(src_path, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"consent/app.py has a syntax error: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
