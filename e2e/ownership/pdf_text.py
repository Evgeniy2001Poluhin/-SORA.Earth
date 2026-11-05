"""Print the text of a PDF, extracted by pypdf (pinned 5.1.0 in CI).

Used by the ownership E2E suite so the saved report's project name and score
are read by a real PDF parser, not by searching raw bytes.
"""
import sys

from pypdf import PdfReader


def main(path: str) -> None:
    reader = PdfReader(path)
    sys.stdout.write("\n".join(page.extract_text() or "" for page in reader.pages))


if __name__ == "__main__":
    main(sys.argv[1])
