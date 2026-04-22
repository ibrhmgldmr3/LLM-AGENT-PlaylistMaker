from src.services.topic_service import generate_subtopics


class DummyProvider:
    def generate_subtopics(self, topic: str, language: str):
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
