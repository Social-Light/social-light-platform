"""Server-side HTML → PDF rendering via headless Chromium (Playwright).

Produces a true, self-contained PDF from a fully self-contained HTML string
(all CSS inline, images embedded as data URIs) — no browser print chrome, with
background colours/gradients preserved. Used for the on-demand report downloads.

Deployment note: the Chromium binary must be installed once with
``python -m playwright install chromium``. Run under a sync WSGI worker
(gunicorn sync/gthread) — the Playwright *sync* API cannot run inside an event
loop, so async ASGI workers won't work here.
"""
import base64
import logging

from django.contrib.staticfiles import finders

logger = logging.getLogger(__name__)


class PDFError(Exception):
    """A user-presentable failure while rendering a PDF."""


def _settle(page, wait_for, wait_ms):
    """Wait for the page to be ready to snapshot: prefer an explicit JS readiness
    flag (e.g. charts fully converted to images), falling back to a fixed pause."""
    if wait_for:
        try:
            page.wait_for_function(wait_for, timeout=15000)
        except Exception:
            logger.warning('PDF readiness flag %r not seen in time; snapshotting anyway', wait_for)
        page.wait_for_timeout(200)   # let the final paint settle
    elif wait_ms:
        page.wait_for_timeout(wait_ms)


def render_html_to_pdf(html, *, landscape=False, wait_ms=0, wait_for=None):
    """Render a self-contained HTML string to PDF bytes (A4). ``html`` must not
    depend on external resources served by our own app (embed images as data URIs,
    inline app CSS); CDN resources load over the network. ``wait_for`` is a JS
    expression polled until truthy before snapshotting (for chart pages);
    ``wait_ms`` is the fixed-pause fallback."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise PDFError('PDF rendering is unavailable — the "playwright" package is not installed.')

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage'])
            try:
                page = browser.new_page()
                page.set_content(html, wait_until='networkidle')
                _settle(page, wait_for, wait_ms)
                pdf = page.pdf(
                    format='A4',
                    landscape=landscape,
                    print_background=True,
                    margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'},
                )
            finally:
                browser.close()
        return pdf
    except PDFError:
        raise
    except Exception as exc:
        # Most common cause: the Chromium binary was never installed.
        msg = str(exc)
        if "Executable doesn't exist" in msg or 'playwright install' in msg:
            raise PDFError('PDF engine not set up — run "python -m playwright install chromium" '
                           'on the server.')
        logger.exception('PDF rendering failed')
        raise PDFError('Could not generate the PDF. Please try again.')


def render_url_to_pdf(url, *, cookies=None, wait_ms=1200, wait_for=None, landscape=False):
    """Navigate headless Chromium to ``url`` (carrying ``cookies`` for auth) and
    return PDF bytes. Use for report pages that depend on app static assets /
    live charts — the page loads exactly as in a browser, and ``page.pdf()``
    applies the page's own ``@media print`` rules (which hide the app chrome).

    Requires the app to serve concurrent requests (gunicorn is configured with
    multiple workers), since this issues a second request to our own server.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise PDFError('PDF rendering is unavailable — the "playwright" package is not installed.')

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage'])
            try:
                context = browser.new_context()
                if cookies:
                    context.add_cookies(cookies)
                page = context.new_page()
                # Emulate print media BEFORE load so the page's @media print rules
                # (which bound chart heights) are active while Chart.js draws.
                page.emulate_media(media='print')
                page.goto(url, wait_until='networkidle', timeout=60000)
                _settle(page, wait_for, wait_ms)
                pdf = page.pdf(
                    format='A4',
                    landscape=landscape,
                    print_background=True,
                    margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'},
                )
            finally:
                browser.close()
        return pdf
    except PDFError:
        raise
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or 'playwright install' in msg:
            raise PDFError('PDF engine not set up — run "python -m playwright install chromium" '
                           'on the server.')
        logger.exception('PDF rendering (navigate) failed for %s', url)
        raise PDFError('Could not generate the PDF. Please try again.')


def static_data_uri(path, mime='image/png'):
    """Return a data: URI for a static asset so it embeds in an offline HTML
    string (Playwright renders via set_content with no server to resolve URLs)."""
    fs_path = finders.find(path)
    if not fs_path:
        return ''
    with open(fs_path, 'rb') as fh:
        b64 = base64.b64encode(fh.read()).decode('ascii')
    return f'data:{mime};base64,{b64}'
