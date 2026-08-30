"""PC-side Lotus BLE discovery helper; never run this on the Surface client."""
import argparse, asyncio, json
from jarvis.integrations.lotus import discover_lamps

async def main() -> None:
    parser=argparse.ArgumentParser(description="Find nearby Bluetooth lamps for Jarvis")
    parser.add_argument("--timeout", type=float, default=8)
    args=parser.parse_args()
    print(json.dumps(await discover_lamps(args.timeout), indent=2))
if __name__ == "__main__": asyncio.run(main())
