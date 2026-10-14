"""Riga di comando (prima dell'interfaccia grafica).

  python3 -m binmirror analizza "/Volumes/SSD/MATERIALI"
  python3 -m binmirror applica  "/Volumes/SSD/MATERIALI" [--bin MATERIALI] [--si] [--forza]
"""
import argparse
import sys

from . import engine
from .resolve_bridge import ResolveError

ICON = {"mkdir": "📁+", "move": "↪ ", "trash": "🗑 ", "create_bin": "🗂+", "move_bin": "↪ ",
        "delete_bin": "🗂-", "move_clip": "🎞↪", "relink": "🔗 ", "import": "⬇ "}


def print_plan(plan):
    if plan.blocked:
        print("⛔ " + plan.blocked)
        return
    if plan.is_empty():
        print("✓ Disco e Resolve sono già allineati.")
    if plan.disk:
        print("\nSU DISCO")
        for a in plan.disk:
            print("  %s %s" % (ICON[a["kind"]], a["why"]))
    if plan.resolve:
        print("\nIN RESOLVE")
        for a in plan.resolve:
            print("  %s %s" % (ICON[a["kind"]], a["why"]))
    for n in plan.notes:
        print("⚠️  " + n)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="binmirror")
    ap.add_argument("comando", choices=["analizza", "applica"])
    ap.add_argument("radice", help="cartella su disco che rispecchia i bin")
    ap.add_argument("--bin", default="", help="bin di Resolve da rispecchiare (vuoto = Master)")
    ap.add_argument("--si", action="store_true", help="applica senza chiedere conferma")
    ap.add_argument("--forza", action="store_true", help="ignora il blocco sui cestini troppo grandi")
    args = ap.parse_args(argv)
    try:
        plan = engine.analyze(args.radice, base=args.bin, force=args.forza)
        print_plan(plan)
        if args.comando == "analizza" or plan.blocked or plan.is_empty():
            return 1 if plan.blocked else 0
        if not args.si and input("\nApplico? [s/N] ").strip().lower() not in ("s", "si", "sì", "y"):
            print("Niente fatto.")
            return 0
        for a, out in engine.apply(args.radice, plan, base=args.bin):
            print("  %s %s → %s" % (ICON[a["kind"]], a["why"], out))
        return 0
    except ResolveError as e:
        print("⛔ Resolve: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
