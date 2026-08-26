"""
fixtures.py – Reusable test data for the RSP test suite.
"""

# A complete normalised document (matches the schema RSP returns)
SAMPLE_DOC = {
    "source":   "HAL",
    "title":    "A Study on Machine Learning",
    "authors":  "Alice Smith, Bob Jones",
    "year":     2022,
    "journal":  "Journal of Artificial Intelligence",
    "abstract": "This paper presents a comprehensive study on machine learning techniques.",
    "url":      "https://hal.science/hal-001",
    "doi":      "10.1234/hal001",
    "keywords": ["machine learning", "classification"],
    "docType":  "ART",
    "language": "en",
}

# Minimal valid saved-search creation body
SAMPLE_SEARCH_PAYLOAD = {
    "name": "My SLR",
    "query": "machine learning",
    "databases": ["hal", "arxiv"],
    "maxResults": 10,
    "schedule": {"type": "manual"},
    "filters": {},
    "validationPapers": [],
}

# Fake HAL JSON response body (2 docs)
HAL_API_RESPONSE_2_DOCS = {
    "response": {
        "numFound": 2,
        "docs": [
            {
                "title_s":           ["First Paper"],
                "authFullName_s":    ["Author One"],
                "producedDateY_i":   2021,
                "journalTitle_s":    "Fake Journal",
                "abstract_s":        ["Abstract one."],
                "uri_s":             "https://hal.science/hal-100",
                "doiId_s":           "10.9999/hal100",
                "keyword_s":         ["ai"],
                "docType_s":         "ART",
                "language_s":        "en",
            },
            {
                "title_s":           ["Second Paper"],
                "authFullName_s":    ["Author Two"],
                "producedDateY_i":   2022,
                "journalTitle_s":    "Another Journal",
                "abstract_s":        ["Abstract two."],
                "uri_s":             "https://hal.science/hal-101",
                "doiId_s":           "10.9999/hal101",
                "keyword_s":         ["ml"],
                "docType_s":         "ART",
                "language_s":        "en",
            },
        ],
    }
}

# Fake arXiv Atom XML response (1 doc)
ARXIV_API_RESPONSE_1_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
    <opensearch:totalResults>1</opensearch:totalResults>
    <entry xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
        <id>http://arxiv.org/abs/2401.00001</id>
        <title>Deep Learning Survey</title>
        <summary>A survey on deep learning approaches.</summary>
        <author><name>Alice Researcher</name></author>
        <published>2024-01-15T00:00:00Z</published>
        <arxiv:doi>10.9999/arxiv001</arxiv:doi>
        <category term="cs.LG"/>
    </entry>
</feed>"""
