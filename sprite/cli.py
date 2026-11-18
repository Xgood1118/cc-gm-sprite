import argparse
import sprite as m


def main():
    ap = argparse.ArgumentParser(description="缩略图雪碧图与坐标表")
        ap.add_argument("--cell", type=int, default=32)
        ap.add_argument("--cols", type=int, default=8)
        ap.add_argument("--out", required=True)
    a = ap.parse_args()
        print("TODO")


if __name__ == "__main__":
    main()
