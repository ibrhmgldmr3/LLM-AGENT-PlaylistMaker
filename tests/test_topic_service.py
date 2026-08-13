from src.services.topic_service import generate_subtopics


class DummyProvider:
    def __init__(self, items=None):
        self.items = items
        self.received_max_items = None

    def generate_subtopics(self, topic: str, language: str, max_items: int = 6):
        self.received_max_items = max_items
        if self.items is not None:
            return self.items
        return [
            "Neural network basics",
            "Neural Network Basics",
            "Model evaluation",
            "Evaluation of models",
        ]


def test_generate_subtopics_deduplicates_and_merges_similar_labels():
    result = generate_subtopics(DummyProvider(), "Deep learning", "en")

    assert len(result) == 3
    assert result[0].normalized_title == "neural-network-basics"
    assert "Neural Network Basics" in result[0].source_titles


def test_subtopics_are_capped_to_max_items():
    """Regresyon: LLM'in dondurdugu liste kirpilmadigi icin kota patlayabiliyordu."""
    provider = DummyProvider(items=[f"Konu {index}" for index in range(30)])

    result = generate_subtopics(provider, "Herhangi bir konu", "tr", max_items=5)

    assert len(result) == 5
    assert provider.received_max_items == 5


def test_turkish_subtopics_are_not_collapsed_by_slugify():
    """Regresyon: ASCII disi karakterler siliniyordu, farkli konular birlesiyordu."""
    provider = DummyProvider(items=["Doğrusal Regresyon", "Çözüm Şeması", "Veri ön işleme"])

    result = generate_subtopics(provider, "Makine öğrenmesi", "tr", max_items=6)

    assert len(result) == 3
    slugs = [item.normalized_title for item in result]
    assert slugs == ["dogrusal-regresyon", "cozum-semasi", "veri-on-isleme"]
