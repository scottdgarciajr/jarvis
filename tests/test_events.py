import pytest
from jarvis.events import Event, EventBus

@pytest.mark.asyncio
async def test_visual_events_fan_out():
    bus=EventBus(); one=bus.subscribe(); two=bus.subscribe()
    await bus.publish(Event("visual_state","lights",{"color":"#8000FF"}))
    assert (await one.get()).data["color"] == "#8000FF"
    assert (await two.get()).state == "lights"
