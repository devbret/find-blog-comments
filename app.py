import argparse
import re
import sys
import time
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

COMMENT_PLATFORM_HINTS = (
    "disqus.com",
    "disqus_thread",
    "disquscdn",
    "commento",
    "commento.io",
    "hyvor",
    "talkyard",
    "remark42",
    "isso",
    "graphcomment",
    "fastcomments",
    "utteranc.es",
    "giscus",
    "facebook.com/plugins/comments",
    "fb-comments",
    "intensedebate.com",
    "livefyre",
    "spot.im",
    "openweb",
    "coral.coralproject",
    "coral-talk",
    "wpdiscuz",
)

COMMENT_CONTAINER_HINTS = (
    "comment",
    "comments",
    "comment-list",
    "commentlist",
    "comment-section",
    "comments-section",
    "comment-area",
    "comments-area",
    "respond",
    "discussion",
    "responses",
)

COMMENT_FALSE_POSITIVES = (
    "no-comments",
    "nocomments",
    "comments-closed",
    "comment-closed",
    "comments-disabled",
)


class CommentFinder:
    def __init__(
        self,
        root_url,
        max_pages=25,
        output="comment_pages.txt",
        delay=1.0,
        timeout=15,
        verbose=True,
    ):
        self.root_url = self._normalize(root_url)
        if not self.root_url:
            raise ValueError(f"Invalid root URL: {root_url!r}")

        self.root_domain = self._registrable_domain(self.root_url)
        self.max_pages = max_pages
        self.output = output
        self.delay = delay
        self.timeout = timeout
        self.verbose = verbose

        self.session = requests.Session()

        self.visited_internal = set()
        self.external_links = set()
        self.checked_external = set()
        self.found_with_comments = []

    @staticmethod
    def _normalize(url):
        if not url:
            return None

        url = url.strip()

        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url

        url, _ = urldefrag(url)
        parsed = urlparse(url)

        if not parsed.netloc:
            return None

        return url

    @staticmethod
    def _registrable_domain(url):
        host = urlparse(url).netloc.lower()

        if host.startswith("www."):
            host = host[4:]

        return host

    def _is_internal(self, url):
        return self._registrable_domain(url) == self.root_domain

    def _is_http(self, url):
        return urlparse(url).scheme in ("http", "https")

    def _log(self, *args):
        if self.verbose:
            print(*args, file=sys.stderr, flush=True)

    def _fetch(self, url):
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as e:
            self._log(f"    ! fetch error: {e}")
            return None, None

        if resp.status_code != 200:
            self._log(f"    ! status {resp.status_code}")
            return None, None

        ctype = resp.headers.get("Content-Type", "")

        if "html" not in ctype.lower():
            self._log(f"    ! not html ({ctype})")
            return None, None

        soup = BeautifulSoup(resp.text, "html.parser")
        return soup, resp.url

    @staticmethod
    def _extract_links(soup, base_url):
        links = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()

            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue

            absolute = urljoin(base_url, href)
            absolute, _ = urldefrag(absolute)
            links.add(absolute)

        return links

    def detect_comments(self, soup):
        html = str(soup).lower()

        for hint in COMMENT_PLATFORM_HINTS:
            if hint in html:
                return True, f"platform/widget: {hint}"

        for tag in soup.find_all(attrs={"id": True}) + soup.find_all(
            attrs={"class": True}
        ):
            ident = " ".join(
                filter(
                    None,
                    [
                        tag.get("id", ""),
                        " ".join(tag.get("class", []))
                        if isinstance(tag.get("class"), list)
                        else tag.get("class", ""),
                    ],
                )
            ).lower()

            if not ident:
                continue

            if any(fp in ident for fp in COMMENT_FALSE_POSITIVES):
                continue

            for hint in COMMENT_CONTAINER_HINTS:
                if re.search(rf"(^|[\s_\-]){re.escape(hint)}([\s_\-]|$)", ident):
                    if tag.find("form") or tag.find("textarea") or len(
                        tag.find_all(["li", "article", "p"])
                    ) >= 1:
                        return True, f"container id/class: {hint}"

        for form in soup.find_all("form"):
            form_id = (
                form.get("id", "") + " " + " ".join(form.get("class", []) or [])
            ).lower()

            if form.find("textarea") and (
                "comment" in form_id or "respond" in form_id or "comment" in html
            ):
                if (
                    "leave a comment" in html
                    or "post a comment" in html
                    or "comment" in form_id
                ):
                    return True, "comment form with textarea"

        for phrase in (
            "leave a comment",
            "leave a reply",
            "post a comment",
            "0 comments",
            "comments (",
            "join the discussion",
        ):
            if phrase in html:
                return True, f"phrase: {phrase!r}"

        return False, ""

    def crawl_internal(self):
        self._log(
            f"=== Crawling up to {self.max_pages} internal pages on "
            f"{self.root_domain} ==="
        )

        queue = deque([self.root_url])
        queued = {self.root_url}

        while queue and len(self.visited_internal) < self.max_pages:
            url = queue.popleft()

            if url in self.visited_internal:
                continue

            self._log(f"[{len(self.visited_internal) + 1}/{self.max_pages}] {url}")

            soup, final_url = self._fetch(url)

            self.visited_internal.add(url)

            if final_url:
                self.visited_internal.add(final_url)

            if soup is None:
                time.sleep(self.delay)
                continue

            for link in self._extract_links(soup, final_url or url):
                if not self._is_http(link):
                    continue

                if self._is_internal(link):
                    if (
                        link not in self.visited_internal
                        and link not in queued
                        and len(queued) < self.max_pages * 5
                    ):
                        queue.append(link)
                        queued.add(link)
                else:
                    self.external_links.add(link)

            time.sleep(self.delay)

        self._log(
            f"=== Visited {len(self.visited_internal)} internal pages, "
            f"found {len(self.external_links)} external links ==="
        )

    def check_external(self):
        self._log(f"=== Checking {len(self.external_links)} external links ===")

        for i, url in enumerate(sorted(self.external_links), 1):
            if url in self.checked_external:
                continue

            self.checked_external.add(url)

            self._log(f"[ext {i}/{len(self.external_links)}] {url}")

            soup, _ = self._fetch(url)

            if soup is None:
                time.sleep(self.delay)
                continue

            has_comments, reason = self.detect_comments(soup)

            if has_comments:
                self._log(f"    >>> COMMENTS FOUND ({reason})")
                self.found_with_comments.append(url)
                self._append_result(url)

            time.sleep(self.delay)

    def _append_result(self, url):
        with open(self.output, "a", encoding="utf-8") as f:
            f.write(url + "\n")

    def run(self):
        open(self.output, "w", encoding="utf-8").close()

        self.crawl_internal()
        self.check_external()

        self._log(
            f"\n=== Done. {len(self.found_with_comments)} pages with comments "
            f"sections written to {self.output} ==="
        )

        return self.found_with_comments


def main():
    parser = argparse.ArgumentParser(
        description="Crawl a domain, find external links, and detect comments sections."
    )

    parser.add_argument("root_url", help="Root URL / domain to start from")

    parser.add_argument(
        "-n",
        "--max-pages",
        type=int,
        default=25,
        help="Number of internal pages to visit (default: 25)",
    )

    parser.add_argument(
        "-o",
        "--output",
        default="comment_pages.txt",
        help="Output text file for matching external URLs (default: comment_pages.txt)",
    )

    parser.add_argument(
        "-d",
        "--delay",
        type=float,
        default=1.0,
        help="Delay in seconds between requests (default: 1.0)",
    )

    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=15,
        help="Per-request timeout in seconds (default: 15)",
    )

    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress progress logging",
    )

    args = parser.parse_args()

    try:
        finder = CommentFinder(
            root_url=args.root_url,
            max_pages=args.max_pages,
            output=args.output,
            delay=args.delay,
            timeout=args.timeout,
            verbose=not args.quiet,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        results = finder.run()
    except KeyboardInterrupt:
        print("\nInterrupted. Partial results saved.", file=sys.stderr)
        sys.exit(130)

    for url in results:
        print(url)


if __name__ == "__main__":
    main()