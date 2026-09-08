import hashlib
from collections import defaultdict



def fingerprint(data):


    text=str(
        (
            data["type"],
            sorted(
                (
                    k,
                    sorted(
                        map(
                            str,
                            v
                        )
                    )
                )

                for k,v
                in data["connections"].items()

            )
        )
    )


    return hashlib.md5(
        text.encode()
    ).hexdigest()




def diff_netlist(
        base_graph,
        new_graph
):


    base_fp=defaultdict(list)

    new_fp=defaultdict(list)



    for n,d in base_graph.nodes(data=True):

        base_fp[
            fingerprint(d)
        ].append(n)



    for n,d in new_graph.nodes(data=True):

        new_fp[
            fingerprint(d)
        ].append(n)




    changed=[]



    for fp,nodes in base_fp.items():

        if fp not in new_fp:

            changed.extend(nodes)



    for fp,nodes in new_fp.items():

        if fp not in base_fp:

            changed.extend(nodes)



    return changed