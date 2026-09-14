#!/usr/bin/env python3


import json
import os
import sys



if len(sys.argv) != 2:

    print(
        "Usage:"
    )

    print(
        "python3 generate_candidates.py <case>"
    )

    sys.exit(1)



case = sys.argv[1]



input_file = (
    f"results/{case}/analysis/"
    "formal_candidates.json"
)


output_dir = (
    f"results/{case}/formal/"
    "candidates"
)


output_file = (
    output_dir
    +
    "/formal_jobs.json"
)



if not os.path.exists(input_file):

    raise FileNotFoundError(
        input_file
    )



os.makedirs(
    output_dir,
    exist_ok=True
)



with open(input_file) as f:

    data = json.load(f)



candidate_groups = data.get(
    "candidates",
    []
)



jobs = []



job_id = 0



for group in candidate_groups:


    base_node = group[
        "base"
    ]


    candidates = group[
        "candidates"
    ]


    for candidate in candidates:


        new_node = candidate[
            "new"
        ]


        job = {

            "job_id":
                job_id,


            "base_node":
                base_node,


            "new_node":
                new_node,


            "base_scope":
                group.get(
                    "base_scope"
                ),


            "base_type":
                group.get(
                    "base_type"
                ),


            "score":
                candidate.get(
                    "score",
                    0
                ),


            "reasons":
                candidate.get(
                    "reasons",
                    []
                ),


            "status":
                "pending"

        }


        jobs.append(
            job
        )


        job_id += 1





output = {

    "case":
        case,


    "total_jobs":
        len(jobs),


    "jobs":
        jobs

}



with open(
    output_file,
    "w"
) as f:


    json.dump(

        output,

        f,

        indent=2

    )



print(
    "Generated formal jobs:"
)

print(
    output_file
)


print(
    "Total candidate pairs:",
    len(jobs)
)