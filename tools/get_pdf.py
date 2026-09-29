"""Opt-in offline tool: search the web for a query and export the first hit as a PDF.

This used to live in util/tools.py as ``getPDF`` and was called at runtime to
ground the patient/examiner models with UpToDate pages. Runtime grounding was
removed (the app must work on networks that block scraping), so the tool now
lives here and is never imported by the app.

Requires the optional dependencies in requirements-optional.txt::

    uv pip install -r requirements-optional.txt
    python tools/get_pdf.py "Uptodate diabetes" output.pdf
"""
import base64
import sys
import time


def get_pdf(query: str, output_pdf: str) -> bool:
    """Search for ``query``, open the first result in headless Chrome, and
    print it to ``output_pdf``. Returns True on success."""
    from googlesearch import search
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--remote-allow-origins=*")  # Required for Chrome 124+
    chrome_options.add_experimental_option("prefs", {
        "plugins.always_open_pdf_externally": True,
        "download.prompt_for_download": False,
    })

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options,
    )
    try:
        print(f"Searching for: {query}")
        search_results = list(search(query, num_results=1))
        if not search_results:
            print("No search results")
            return False

        driver.get(search_results[0])
        time.sleep(5)

        result = driver.execute_cdp_cmd("Page.printToPDF", {
            "landscape": False,
            "displayHeaderFooter": False,
            "printBackground": True,
            "preferCSSPageSize": True,
            "margin": {"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"},
        })
        with open(output_pdf, "wb") as f:
            f.write(base64.b64decode(result["data"]))
        return True
    except Exception as e:
        print(f"An error occurred: {e}")
        return False
    finally:
        driver.quit()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(0 if get_pdf(sys.argv[1], sys.argv[2]) else 1)
