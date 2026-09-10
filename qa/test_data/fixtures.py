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


def _ebsco_record(i):
    """One EDS Record in the nested shape the EDS API returns."""
    return {
        "ResultId": i + 1,
        "Header": {"DbId": "edstst", "An": f"edstst.{i}", "PubTypeId": "academicJournal"},
        "PLink": f"http://search.ebscohost.com/login.aspx?direct=true&db=edstst&AN={i}",
        "Items": [
            {"Name": "Abstract", "Group": "Ab", "Data": f"Abstract <i>{i}</i> from EDS."},
        ],
        "RecordInfo": {
            "BibRecord": {
                "BibEntity": {
                    "Titles": [{"TitleFull": f"EBSCO Paper {i}", "Type": "main"}],
                    "Identifiers": [{"Type": "doi", "Value": f"10.9999/ebsco{i}"}],
                    "Languages": [{"Text": "English", "Code": "eng"}],
                    "Subjects": [{"SubjectFull": "information retrieval", "Type": "general"}],
                },
                "BibRelationships": {
                    "HasContributorRelationships": [
                        {"PersonEntity": {"Name": {"NameFull": f"Author {i}"}}}
                    ],
                    "IsPartOfRelationships": [
                        {
                            "BibEntity": {
                                "Titles": [{"TitleFull": "EBSCO Journal", "Type": "main"}],
                                "Dates": [{"Y": str(2020 + i), "M": "06", "Type": "published"}],
                            }
                        }
                    ],
                },
            }
        },
    }


def ebsco_api_response(n=2):
    """Fake EDS /Search response body containing n records."""
    return {
        "SearchResult": {
            "Statistics": {"TotalHits": n},
            "Data": {"Records": [_ebsco_record(i) for i in range(n)]},
        }
    }


EBSCO_API_RESPONSE_2_DOCS = ebsco_api_response(2)
