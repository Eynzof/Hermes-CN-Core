from run_agent import AIAgent


def test_forced_native_images_survive_adapter_preparation(monkeypatch):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"image_input_mode": "native"}})
    agent = AIAgent.__new__(AIAgent)
    agent.provider = "custom:local"
    agent.model = "unknown-local-vision-model"
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "Describe this image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]}]
    assert agent._prepare_messages_for_non_vision_model(messages) == messages
