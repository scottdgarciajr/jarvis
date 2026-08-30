import pytest
from jarvis.integrations.lotus import MockLamp
from jarvis.tools.lotus_lamp import LotusTools
from jarvis.tools.registry import ToolRegistry

@pytest.mark.asyncio
async def test_lotus_rgb_is_validated_and_emits_visual_event():
    lamp=MockLamp(); registry=ToolRegistry()
    for tool in LotusTools(lamp, {}).all(): registry.register(tool)
    result=await registry.call("lotus_set_rgb", {"red":128,"green":0,"blue":255})
    assert lamp.commands == [("rgb",128,0,255)]
    light_events=[event for event in result.events if event.type == "visual_state"]
    assert light_events[0].data["color"] == "#8000FF"
    with pytest.raises(Exception): await registry.call("lotus_set_rgb", {"red":999,"green":0,"blue":0})

@pytest.mark.asyncio
async def test_lotus_brightness_scales_current_color():
    lamp=MockLamp(); registry=ToolRegistry()
    for tool in LotusTools(lamp, {}).all(): registry.register(tool)
    await registry.call("lotus_set_rgb", {"red":100,"green":50,"blue":20})
    result=await registry.call("lotus_set_brightness", {"brightness":50})
    assert lamp.commands[-1] == ("rgb",50,25,10)
    assert result.data["color"] == "#32190A"

@pytest.mark.asyncio
async def test_lotus_effect_emits_visual_color():
    lamp=MockLamp(); registry=ToolRegistry()
    for tool in LotusTools(lamp, {}).all(): registry.register(tool)
    result=await registry.call("lotus_set_effect", {"effect":"candelights"})
    light_events=[event for event in result.events if event.type == "visual_state"]
    assert light_events[0].data["effect"] == "candlelight"
    assert light_events[0].data["color"] == "#481C07"

@pytest.mark.asyncio
async def test_unknown_tool_is_not_callable():
    with pytest.raises(PermissionError): await ToolRegistry().call("shell", {"command":"whoami"})
