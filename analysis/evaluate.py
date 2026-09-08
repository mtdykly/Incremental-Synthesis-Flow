def evaluate(
        predicted,
        actual
):


    predicted=set(predicted)

    actual=set(actual)


    inter=(
        predicted
        &
        actual
    )


    recall=(
        len(inter)
        /
        len(actual)
        if actual else 0
    )


    precision=(
        len(inter)
        /
        len(predicted)
        if predicted else 0
    )


    return {

        "recall":recall,

        "precision":precision,

        "actual_size":len(actual),

        "predicted_size":len(predicted)

    }