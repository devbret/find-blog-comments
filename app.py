import argparse
import re
import sys
import time
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup, NavigableString

COMMENT_PLATFORMS = (
    (
        "Disqus",
        ("disqus.com", "disquscdn.com"),
        ("disqus_thread", "disqus-thread", "disqus"),
    ),
    (
        "Commento",
        ("commento.io", "commento.js", "comentario"),
        ("commento", "comentario"),
    ),
    (
        "Hyvor Talk",
        ("talk.hyvor.com",),
        ("hyvor-talk-comments", "hyvor-talk", "hyvortalk"),
    ),
    ("Talkyard", ("talkyard.net", "talkyard.io"), ("talkyard-comments", "ed-comments")),
    ("Remark42", ("remark42",), ("remark42",)),
    ("Isso", ("isso.js", "isso.min.js", "/isso/"), ("isso-thread", "isso_thread")),
    ("GraphComment", ("graphcomment.com",), ("graphcomment",)),
    ("FastComments", ("fastcomments.com",), ("fastcomments",)),
    ("utterances", ("utteranc.es",), ("utterances", "utterances-frame")),
    ("giscus", ("giscus.app",), ("giscus", "giscus-frame")),
    ("Facebook Comments", ("facebook.com/plugins/comments",), ("fb-comments",)),
    ("IntenseDebate", ("intensedebate.com",), ("intensedebate", "idc-container")),
    ("Livefyre", ("livefyre.com",), ("livefyre",)),
    (
        "OpenWeb / Spot.IM",
        ("spot.im", "openweb.com", "spotim"),
        ("spotim", "spot-im", "ow-comments"),
    ),
    (
        "Coral",
        ("coralproject.net", "coral-talk"),
        ("coral_thread", "coral-talk", "coralstreamembed"),
    ),
    ("wpDiscuz", ("wpdiscuz",), ("wpdiscuz", "wpdcom", "wpd-thread")),
    ("Cusdis", ("cusdis.com",), ("cusdis_thread", "cusdis")),
    ("Waline", ("waline",), ("waline", "wl-comment")),
    ("Twikoo", ("twikoo",), ("twikoo", "tk-comments")),
    ("Gitalk", ("gitalk",), ("gitalk", "gitalk-container")),
    ("Vuukle", ("vuukle.com",), ("vuukle",)),
    ("Muut", ("muut.com",), ("muut",)),
    ("HyperComments", ("hypercomments.com",), ("hypercomments",)),
    ("Viafoura", ("viafoura.co", "viafoura.net"), ("viafoura", "vf-conversations")),
)

EMBED_TAGS = ("script", "iframe", "link")

COMMENT_CONTAINER_HINTS = (
    "comment",
    "comments",
    "commentlist",
    "commentform",
    "respond",
    "discussion",
    "responses",
)

COMMENT_ITEM_HINTS = ("comment",)
COMMENT_ITEM_TAGS = ("li", "article", "div", "section")
COMMENT_ITEM_EXCLUDE = (
    "comment-form",
    "commentform",
    "comment-respond",
    "respond",
    "comment-reply",
    "comment-reply-link",
    "comment-reply-title",
    "comment-notes",
    "comment-count",
    "comments-count",
    "comments-link",
    "comment-policy",
    "comment-awaiting-moderation",
)
COMMENT_ITEM_MIN_TEXT = 20

COMMENT_FORM_HINTS = ("comment", "comments", "commentform", "respond", "reply")

COMMENT_PHRASES = (
    "leave a comment",
    "leave a reply",
    "post a comment",
    "add a comment",
    "write a comment",
    "add your comment",
    "join the discussion",
)

COMMENT_CLOSED_PHRASES = (
    "comments are closed",
    "comments are now closed",
    "comments have been closed",
    "comments are disabled",
    "comments have been disabled",
    "commenting is disabled",
    "commenting has been disabled",
    "commenting is closed",
    "commenting has been turned off",
    "comments are turned off",
    "comments are off for this",
    "comments are not allowed",
    "closed for comments",
    "comment section is closed",
    "discussion is closed",
)

COMMENT_EMPTY_PHRASES = (
    "no comments yet",
    "be the first to comment",
    "be the first to leave a comment",
)

SCORE_PLATFORM = 100
SCORE_CONTAINER_STRONG = 60
SCORE_CONTAINER_WEAK = 25
SCORE_COMMENT_FORM = 60
SCORE_SCHEMA = 60
SCORE_COUNT_NONZERO = 40
SCORE_COUNT_ZERO = 30
SCORE_PHRASE = 30
SCORE_HEADING = 30
COMMENT_SCORE_THRESHOLD = 60

_COUNT_PATTERNS = (
    re.compile(r"\b(\d[\d,]*)\s+comments?\b"),
    re.compile(r"\bcomments?\s*\(\s*(\d[\d,]*)\s*\)"),
    re.compile(r"\bcomments?\s*:\s*(\d[\d,]*)\b"),
)

_HEADING_RE = re.compile(
    r"^(?:\d[\d,]*\s+)?(?:comments?|responses|discussion|replies)"
    r"(?:\s*\(\s*\d[\d,]*\s*\))?$"
)

_SCHEMA_COMMENT_RE = re.compile(r"\bcomment\b", re.I)

_NON_TEXT_TAGS = frozenset(
    {"script", "style", "noscript", "template", "head", "title", "meta", "link"}
)


def _attr_text(value):
    if not value:
        return ""

    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value).lower()

    return str(value).lower()


def _ident(tag):
    parts = []

    if tag.name and "-" in tag.name:
        parts.append(tag.name.lower())

    parts.append(_attr_text(tag.get("id")))
    parts.append(_attr_text(tag.get("class")))

    return " ".join(p for p in parts if p)


def _visible_text(soup):
    parts = []

    for string in soup.find_all(string=True):
        if type(string) is not NavigableString:
            continue

        parent = string.parent.name if string.parent else ""

        if parent in _NON_TEXT_TAGS:
            continue

        text = string.strip()

        if text:
            parts.append(text)

    return " ".join(" ".join(parts).split()).lower()


COMMENT_FALSE_POSITIVES = (
    "no-comments",
    "nocomments",
    "comments-closed",
    "comment-closed",
    "comments-disabled",
    "comments-off",
)

HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def _compile_tokens(tokens):
    ordered = sorted(tokens, key=len, reverse=True)
    alternation = "|".join(re.escape(token) for token in ordered)

    return re.compile(rf"(^|[\s_\-])({alternation})([\s_\-]|$)")


def _token_match(pattern, ident):
    match = pattern.search(ident) if ident else None

    return match.group(2) if match else None


def _collapse(text):
    return " ".join(text.split()).lower()


_PLATFORM_PATTERNS = tuple(
    (name, fragments, _compile_tokens(tokens))
    for name, fragments, tokens in COMMENT_PLATFORMS
)
_CONTAINER_RE = _compile_tokens(COMMENT_CONTAINER_HINTS)
_ITEM_RE = _compile_tokens(COMMENT_ITEM_HINTS)
_ITEM_EXCLUDE_RE = _compile_tokens(COMMENT_ITEM_EXCLUDE)
_FORM_RE = _compile_tokens(COMMENT_FORM_HINTS)
_FALSE_POSITIVE_RE = _compile_tokens(COMMENT_FALSE_POSITIVES)


def _schema_comment(tag):
    for attr in ("itemprop", "itemtype", "typeof", "role"):
        value = _attr_text(tag.get(attr))

        if value and _SCHEMA_COMMENT_RE.search(value):
            return True

    return False


def _is_comment_item(tag):
    if tag.name not in COMMENT_ITEM_TAGS:
        return False

    ident = _ident(tag)

    if not _token_match(_ITEM_RE, ident):
        return False

    if _token_match(_ITEM_EXCLUDE_RE, ident):
        return False

    return len(tag.get_text(" ", strip=True)) >= COMMENT_ITEM_MIN_TEXT


def _container_evidence(tag):
    if tag.find("textarea"):
        return SCORE_CONTAINER_STRONG, "with a reply box"

    if tag.find(_is_comment_item):
        return SCORE_CONTAINER_STRONG, "with posted comments"

    if tag.find(["li", "article", "p"]):
        return SCORE_CONTAINER_WEAK, "with unidentified content"

    return 0, ""


def _comment_form_hint(form):
    textarea = form.find("textarea")

    if textarea is None:
        return None

    own = " ".join(
        part
        for part in (
            _ident(form),
            _attr_text(form.get("action")),
            _attr_text(form.get("name")),
            _ident(textarea),
            _attr_text(textarea.get("name")),
        )
        if part
    )

    hint = _token_match(_FORM_RE, own)

    if hint:
        return hint

    placeholder = _attr_text(textarea.get("placeholder"))

    if "comment" in placeholder or "reply" in placeholder:
        return "placeholder"

    return None


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
        text = _visible_text(soup)

        for phrase in COMMENT_CLOSED_PHRASES:
            if phrase in text:
                return False, f"comments closed: {phrase!r}"

        signals = {}

        def record(category, score, label):
            if score > signals.get(category, (0, ""))[0]:
                signals[category] = (score, label)

        self._scan_elements(soup, record)
        self._scan_text(text, record)

        total = sum(score for score, _ in signals.values())

        if total < COMMENT_SCORE_THRESHOLD:
            return False, f"score {total}" if total else ""

        ranked = sorted(signals.values(), reverse=True)
        reasons = ", ".join(label for _, label in ranked)

        return True, f"score {total}: {reasons}"

    @staticmethod
    def _scan_elements(soup, record):
        for tag in soup.find_all(True):
            ident = _ident(tag)

            urls = " ".join(
                _attr_text(value)
                for name, value in tag.attrs.items()
                if name.startswith("data-")
                or (name in ("src", "href") and tag.name in EMBED_TAGS)
            )

            for name, fragments, token_re in _PLATFORM_PATTERNS:
                if urls and any(fragment in urls for fragment in fragments):
                    record("platform", SCORE_PLATFORM, f"platform: {name}")
                    break

                if _token_match(token_re, ident):
                    record("platform", SCORE_PLATFORM, f"platform: {name}")
                    break

            if _schema_comment(tag):
                record("schema", SCORE_SCHEMA, "schema.org Comment markup")

            if tag.name in HEADING_TAGS:
                heading = _collapse(tag.get_text(" ", strip=True))

                if _HEADING_RE.match(heading):
                    record("heading", SCORE_HEADING, f"heading: {heading!r}")

            if tag.name == "form":
                form_hint = _comment_form_hint(tag)

                if form_hint:
                    record("form", SCORE_COMMENT_FORM, f"comment form ({form_hint})")

            hint = _token_match(_CONTAINER_RE, ident)

            if hint and not _token_match(_FALSE_POSITIVE_RE, ident):
                score, detail = _container_evidence(tag)

                if score:
                    record("container", score, f"container {hint!r} {detail}")

    @staticmethod
    def _scan_text(text, record):
        for phrase in COMMENT_PHRASES:
            if phrase in text:
                record("phrase", SCORE_PHRASE, f"phrase: {phrase!r}")
                break

        count = None

        for pattern in _COUNT_PATTERNS:
            for match in pattern.finditer(text):
                value = int(match.group(1).replace(",", ""))
                count = value if count is None else max(count, value)

        if count:
            record("count", SCORE_COUNT_NONZERO, f"{count} comments listed")
        elif count == 0:
            record("count", SCORE_COUNT_ZERO, "empty comment section")

        for phrase in COMMENT_EMPTY_PHRASES:
            if phrase in text:
                record("count", SCORE_COUNT_ZERO, f"phrase: {phrase!r}")
                break

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
