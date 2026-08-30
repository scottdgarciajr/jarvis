from jarvis.memory import MemoryStore

def test_explicit_memory_and_conversation_are_separate(tmp_path):
    store=MemoryStore(tmp_path / "jarvis.sqlite3")
    memory=store.add_memory("My favorite color is purple")
    store.add_conversation("s1", "user", "What is my favorite color?")
    assert memory.id
    assert [x.text for x in store.search_memories("favorite color")] == ["My favorite color is purple"]
    assert store.recent_conversation("s1") == [{"role":"user", "content":"What is my favorite color?"}]
