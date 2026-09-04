from pa.open_session.scan import scan_open

__all__ = ["scan_open"]


def main() -> None:
    from pa.clock import MarketClock
    from pa.config import get_settings

    tape = scan_open(get_settings(), MarketClock())
    strongest = tape.get("strongest")
    print(tape.get("phase"), tape.get("note"))
    if strongest:
        print(strongest.get("text"))
    else:
        print("no strongest signal")


if __name__ == "__main__":
    main()
