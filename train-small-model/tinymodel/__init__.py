"""tinymodel: a teaching-sized language model you can train from scratch on a laptop CPU.

Each module is one step of the pipeline (see notes.md):

    dataset.py   -> Step 1: build or load a custom dataset and split it
    tokenizer.py -> Step 2: turn text into numbers (and back)
    model.py     -> Step 3: the neural network (a tiny GPT-style transformer)
    train.py     -> Step 4: the training loop
    evaluate.py  -> Step 5: measure how good the model is on data it never saw
    generate.py  -> Step 6: use the trained model (inference)
"""
