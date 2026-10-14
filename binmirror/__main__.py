"""Riga di comando (l'app con la finestra usa lo stesso motore).

  python3 -m binmirror analizza "/Volumes/SSD/MATERIALI"
  python3 -m binmirror applica  "/Volumes/SSD/MATERIALI" [--bin MATERIALI] [--si] [--forza]
"""
import argparse
import sys

from . import engine
from .resolve_bridge import ResolveError

ICON = {"mkdir": "📁+", "move": "↪ ", "trash": "🗑 ", "create_bin": "🗂+", "move_bin": "↪ ",
        "delete_bin": "🗂-", "delete_clips": "🎞-", "move_clip": "🎞↪", "relink": "🔗 ",
        "import": "⬇ "}


def print_plan(plan):
    if plan.blocked:
        print("⛔ " + plan.blocked)
        if plan.can_force:
            print("   (rilancia con --forza se è tutto voluto)")
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
    if plan.choices:
        print("\nDA DECIDERE")
        for c in plan.choices:
            print("  ❓ " + c["question"])
            for i, o in enumerate(c["options"], 1):
                mark = " (proposta)" if o["key"] == c["default"] else ""
                print("     %d. %s%s" % (i, o["label"], mark))
    for n in plan.notes:
        print("⚠️  " + n)


def ask_choices(plan, auto):
    sel = {}
    for c in plan.choices:
        keys = [o["key"] for o in c["options"]]
        if auto:
            sel[c["id"]] = c["default"]
            continue
        default = keys.index(c["default"]) + 1
        ans = input("\n%s\nScelta [1-%d, invio = %d]: " % (c["question"], len(keys), default)).strip()
        sel[c["id"]] = keys[int(ans) - 1] if ans.isdigit() and 1 <= int(ans) <= len(keys) \
            else c["default"]
    return sel


def main(argv=None):
    ap = argparse.ArgumentParser(prog="binmirror")
    ap.add_argument("comando", choices=["analizza", "applica"])
    ap.add_argument("radice", help="cartella su disco che rispecchia i bin")
    ap.add_argument("--bin", default="", help="bin di Resolve da rispecchiare (vuoto = Master)")
    ap.add_argument("--si", action="store_true",
                    help="applica senza chiedere (alle domande usa la risposta proposta)")
    ap.add_argument("--forza", action="store_true",
                    help="supera i blocchi che si possono superare (progetto diverso, cestino grande)")
    args = ap.parse_args(argv)
    try:
        plan = engine.analyze(args.radice, base=args.bin, force=args.forza)
        print_plan(plan)
        if args.comando == "analizza" or plan.blocked or plan.is_empty():
            return 1 if plan.blocked else 0
        if not args.si and input("\nApplico? [s/N] ").strip().lower() not in ("s", "si", "sì", "y"):
            print("Niente fatto.")
            return 0
        sel = ask_choices(plan, args.si)
        # Con --si si applica subito dopo l'analisi: il secondo controllo sarebbe tempo perso.
        for a, out in engine.apply(args.radice, plan, base=args.bin, selections=sel,
                                   recheck=not args.si):
            print("  %s %s → %s" % (ICON[a["kind"]], a["why"], out))
        return 0
    except engine.PlanChanged as e:
        print("⛔ " + str(e))
        print_plan(e.plan)
        return 1
    except engine.Busy as e:
        print("⛔ " + str(e))
        return 1
    except ResolveError as e:
        print("⛔ Resolve: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
