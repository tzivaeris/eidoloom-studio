"""Command line for the box tool."""
import argparse

import box


def build_parser():
    parser = argparse.ArgumentParser(prog="box")
    parser.add_argument("items", nargs="*", help="items to put in the box")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    b = box.new_box()
    for item in args.items:
        print(box.add_item(b, item))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
