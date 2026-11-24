"""Build the analysis PDF from signals_log.jsonl (HTML via report_html.py, then Chromium print)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def make_pdf(src_jsonl, out_pdf):
    html_path = os.path.splitext(out_pdf)[0] + '.html'
    subprocess.run([sys.executable, os.path.join(HERE, 'report_html.py'), src_jsonl, html_path],
                   check=True, timeout=180)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox'])
        page = browser.new_page()
        page.goto('file://' + os.path.abspath(html_path), wait_until='load')
        page.pdf(path=out_pdf, format='A4', print_background=True,
                 margin={'top': '12mm', 'bottom': '12mm', 'left': '10mm', 'right': '10mm'})
        browser.close()
    return out_pdf
