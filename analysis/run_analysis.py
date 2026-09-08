import sys
import json
import os


from netlist_graph import NetlistGraph

from netlist_diff import diff_netlist

from cone import fanout_cone



if len(sys.argv)!=2:

    print(
        "usage: run_analysis.py <case>"
    )

    exit(1)



case=sys.argv[1]


base_json=f"results/{case}/base/design.json"

new_json=f"results/{case}/new/design.json"


out=f"results/{case}/analysis"


os.makedirs(
    out,
    exist_ok=True
)



print("Loading base...")

base=NetlistGraph(
    base_json,
    "riscv_core"
)



print("Loading new...")

new=NetlistGraph(
    new_json,
    "riscv_core"
)



print("Diff netlist...")


changed=diff_netlist(
    base.graph,
    new.graph
)



print(
    "Changed cells:",
    len(changed)
)



cone=fanout_cone(
    new.graph,
    changed
)


print(
    "Affected cone:",
    len(cone)
)



with open(
    f"{out}/ground_truth.json",
    "w"
) as f:


    json.dump(

        {
            "changed_cells":
                changed,

            "cone_size":
                len(cone),

            "cone":
                list(cone)

        },

        f,

        indent=2

    )