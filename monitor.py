
import time
import urllib.request
import urllib.error

WEBSITES = {
    "Google": "https://www.google.com",
    "GitHub": "https://www.github.com",
}

def check_website(name, url):
    start = time.perf_counter()

    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "SiteWatch-Monitor"}
        )

        with urllib.request.urlopen(request, timeout=5) as response:
            code = response.status

        status = "UP" if code < 500 else "DOWN"
        error = ""

    except urllib.error.HTTPError as exc:
        code = exc.code
        status = "UP" if code < 500 else "DOWN"
        error = f"HTTP error: {code}"

    except Exception as exc:
        code = None
        status = "DOWN"
        error = str(exc)

    elapsed = round((time.perf_counter() - start) * 1000, 2)

    return {
        "Website": name,
        "URL": url,
        "Status": status,
        "HTTP Code": code,
        "Response Time (ms)": elapsed,
        "Error": error,
        "Checked At": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

def check_all_websites():
    return [
        check_website(name, url)
        for name, url in WEBSITES.items()
    ]
