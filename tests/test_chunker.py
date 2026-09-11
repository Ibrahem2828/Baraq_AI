from app.rag.chunker import ArabicAwareChunker
from app.rag.extractors import ExtractedDocument, ExtractedPage


def test_chunker_preserves_page_metadata() -> None:
    document = ExtractedDocument(
        pages=[ExtractedPage(1, "هذه جملة أولى. هذه جملة ثانية. " * 20, "الفصل الأول")],
        metadata={},
    )
    chunks = ArabicAwareChunker(chunk_size=180, overlap=30).chunk(document)
    assert len(chunks) > 1
    assert all(item.page_number == 1 for item in chunks)
    assert all(item.section_title == "الفصل الأول" for item in chunks)
    assert [item.chunk_index for item in chunks] == list(range(len(chunks)))
