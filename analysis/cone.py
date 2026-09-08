import networkx as nx



def fanout_cone(
        graph,
        roots
):


    result=set()


    for r in roots:


        if r not in graph:

            continue


        result.add(r)


        result.update(
            nx.descendants(
                graph,
                r
            )
        )


    return result