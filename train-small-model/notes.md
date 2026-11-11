# Training a small model from scratch: step-by-step notes

These notes teach you how to train a small language model on your own data, starting from zero.
No machine-learning background is assumed. Every new word is defined the first time it appears
(and collected in the [Glossary](#glossary) at the end), and every step explains **why** it exists,
not just which command to run.

You will build a tiny GPT-style model (the same family of model as ChatGPT or Claude, about a
million times smaller), train it on your laptop CPU in about a minute, and watch it learn to add
two-digit numbers it has never seen before. Then you will point the same code at your own text.

Read the notes in order. Each part builds on the one before it.

| Part | What you learn | Code |
|------|----------------|------|
| [0. The big picture](#part-0-the-big-picture) | What a model is and what "training" means | – |
| [1. Setup and tensors](#part-1-setup-and-tensors) | Install the tools, meet the tensor | – |
| [2. The dataset](#part-2-the-dataset) | Make a custom dataset, split it train/val/test | `tinymodel/dataset.py` |
| [3. Tokenization](#part-3-tokenization) | Turn text into numbers | `tinymodel/tokenizer.py` |
| [4. Batches](#part-4-inputs-targets-and-batches) | What the model is actually asked to predict | `tinymodel/train.py` (`get_batch`) |
| [5. A first model: the bigram](#part-5-a-first-model-the-bigram) | Embeddings, logits, softmax, loss | a snippet in these notes |
| [6. The transformer](#part-6-the-transformer) | Attention, blocks, the full model | `tinymodel/model.py` |
| [7. The training loop](#part-7-the-training-loop) | Gradient descent, backprop, optimizers | `tinymodel/train.py` |
| [8. Evaluation](#part-8-evaluation) | Over/underfitting, perplexity, accuracy | `tinymodel/evaluate.py` |
| [9. Inference](#part-9-inference) | Generating text, temperature | `tinymodel/generate.py` |
| [10. Your own data](#part-10-training-on-your-own-data) | Repeat everything on your text | `examples/my_text.txt` |
| [11. Experiments](#part-11-experiments-to-try) | Things to change and what to look for | – |

---

## Part 0: The big picture

### What is a "model"?

A **model** is a function with adjustable knobs. It takes an input (here: some text) and produces
an output (here: a guess about what character comes next). The knobs are numbers called
**parameters** or **weights**. Our model has about 100,000 of them; large language models have
hundreds of billions.

At the start, the knobs are set randomly, so the model's guesses are random. **Training** means
adjusting the knobs, a tiny bit at a time, so the guesses get better.

### How do we know which way to turn the knobs?

1. Show the model an example where we know the right answer.
2. Measure how wrong its guess was with a single number called the **loss** (lower is better).
3. Use calculus to work out, for every knob, "if I turn this knob up a little, does the loss go up
   or down, and by how much?" That collection of answers is the **gradient**.
4. Turn every knob a small step in the direction that makes the loss go down.
5. Repeat thousands of times.

That is the whole idea. Everything else in these notes is detail about doing those five steps well.

An everyday analogy: you are blindfolded on a hilly field and want to reach the lowest point. You
can't see, but you can feel the slope under your feet (the gradient). So you take a small step
downhill, feel again, take another step, and so on. The **learning rate** is the length of your
step: too short and it takes forever; too long and you overshoot the valley.

### What will our model learn?

GPT-style models are trained on one task: **predict the next piece of text**. Given `23+58=`, a
good model predicts `8`, then (given `23+58=8`) predicts `1`, then a newline. It sounds limited,
but predicting the next character well forces the model to learn the structure of the data. To
predict the answer to an addition problem, it has to learn something like addition.

### The pipeline

```
 your data ──► split ──► tokenize ──► batches ──► model ──► loss ──► update weights ──┐
                                                    ▲                                 │
                                                    └────────── repeat ───────────────┘
 then:  held-out data ──► evaluate      and      prompt ──► generate (inference)
```

The README has a more detailed diagram. Now let's build each box.

---

## Part 1: Setup and tensors

### Install

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/) (a fast Python package manager).

```bash
cd train-small-model
uv sync            # creates .venv/ and installs PyTorch
uv run pytest -q   # 7 quick checks; if these pass, your setup works
```

**PyTorch** is the library that does the heavy lifting: it stores numbers in tensors, does the
maths fast, and (most importantly) computes gradients for us automatically.

### What is a tensor?

A **tensor** is a grid of numbers with a fixed **shape**. A single number is a 0-d tensor, a list is
1-d, a table is 2-d, a stack of tables is 3-d. Almost every bug in ML code is a shape bug, so the
code in this repo writes the shape next to every important line, like `# (B, T, C)`.

Try this in `uv run python`:

```python
import torch

x = torch.tensor([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
print(x.shape)  # torch.Size([2, 3])  -> 2 rows, 3 columns
print(x.sum(dim=1))  # tensor([ 6., 15.])  -> sum along each row
print(x @ x.T)  # matrix multiply: (2,3) @ (3,2) -> (2,2)
```

Three letters appear everywhere in the model code:

* **B**: batch size, how many examples we process at once.
* **T**: time, i.e. the number of characters in each example (the sequence length).
* **C**: channels, the length of the vector that represents each character.

### Automatic gradients, in five lines

This is the magic that makes training possible. Run it:

```python
import torch

w = torch.tensor(3.0, requires_grad=True)  # a "knob"; PyTorch will track how it is used
loss = (w * 2 - 10) ** 2  # some measure of wrongness; it is 0 when w == 5
loss.backward()  # compute d(loss)/d(w)
print(w.grad)  # tensor(-16.) -> negative: increasing w lowers the loss
```

`loss.backward()` worked out the slope for us. A real model does the same thing for 100,000 knobs
at once. That process is called **backpropagation**.

---

## Part 2: The dataset

**File:** `tinymodel/dataset.py`

### Why does data come first?

A model can only learn patterns that are in its data. If you train on cooking recipes, it will
write recipes. "Garbage in, garbage out" is the first law of machine learning. Choosing and
cleaning the data is usually the most important decision in a real project.

### Our custom dataset: addition problems

We start with a dataset we generate ourselves:

```
12+45=57
70+57=127
48+95=143
```

All 10,000 problems `a+b=c` with `a` and `b` from 0 to 99. Why this instead of, say, Shakespeare?

* **It's small and fast.** 75,000 characters train in about a minute on a CPU.
* **Answers are checkable.** With prose, "is the output good?" is a matter of taste. With
  addition, `23+58=81` is either right or wrong. That makes evaluation (Part 8) concrete.
* **It tests real learning.** We can hold back problems the model never sees, and check whether it
  learned *addition* or just memorised a table.

Generate it:

```bash
uv run python -m tinymodel.dataset addition
```

```
10,000 problems -> train 8,000 / val 1,000 / test 1,000
  wrote data/addition/train.txt  (74,348 characters)
  wrote data/addition/val.txt  (9,260 characters)
  wrote data/addition/test.txt  (9,287 characters)
```

### Train, validation and test: the three splits

This is one of the most important ideas in machine learning, so slow down here.

* **Training set (80%)**: the only data the model learns from.
* **Validation set (10%)**: data we check *during* training to see how we are doing, and use to
  make choices (when to stop, which settings work best). The model never learns from it.
* **Test set (10%)**: data we look at *once*, at the very end, to report the final score.

Why not test on the training data? Because a model can do well on data it has seen simply by
memorising it, like a student who memorised last year's exam answers. That tells you nothing about
how it will do on new questions. Held-out data is the only honest measure.

Why two held-out sets instead of one? Every time you look at the validation score and change
something because of it, you are slowly tuning your choices to that particular set. The test set
stays untouched so the final number is not biased by those choices.

Look at how the code does it:

```python
problems = [f"{a}+{b}={a + b}" for a in range(100) for b in range(100)]
random.Random(seed).shuffle(problems)  # shuffle BEFORE splitting
train, val, test = split_list(problems, val_frac=0.1, test_frac=0.1)
```

Two details worth noticing:

1. **Shuffle before splitting.** Without the shuffle, the test set would be problems like `0+0` to
   `9+99` only, and the training set would never contain small numbers on the left.
2. **A fixed seed** (`Random(seed)`) makes the shuffle the same every run, so results are
   repeatable. Reproducibility matters when you are trying to find out whether a change helped.

`tests/test_pipeline.py::test_splits_do_not_overlap` checks that no problem appears in two splits.
If it did, the model could "cheat" on the test. This mistake is called **data leakage**, and it is
the most common way people fool themselves about how good a model is.

### Bringing your own text

Any plain-text file works:

```bash
uv run python -m tinymodel.dataset text --input path/to/your.txt --name my_text
```

For text we can't shuffle individual characters (that would destroy the meaning), so we keep the
first 90% for training and the last 10% for validation. Part 10 walks through this.

**Data tips for your own files:**

* More is better. A few kilobytes will be memorised; a few megabytes starts to get interesting.
* Make it consistent. One style, one language, one kind of document learns faster than a mixture.
* Clean it. Remove junk like page numbers, HTML tags or repeated headers, or the model will
  faithfully learn to produce them.

---

## Part 3: Tokenization

**File:** `tinymodel/tokenizer.py`

### Why?

Neural networks only do arithmetic. They can't read `"7+12=19"`. So we need a reversible way to
turn text into a list of integers and back. The pieces we split text into are called **tokens**,
and the program that does the splitting is the **tokenizer**.

### Character-level tokenization

The simplest scheme: every distinct character is a token. The list of all tokens is the
**vocabulary**, and its length is the **vocabulary size** (`vocab_size`).

```bash
uv run python -m tinymodel.tokenizer
```

```
vocabulary: ['\n', '+', '1', '2', '7', '9', '=']
encode('7+12=19') -> [4, 1, 2, 3, 6, 2, 5]
decode(...)       -> 7+12=19
```

The core of it is two dictionaries:

```python
chars = sorted(set(text))  # every distinct character, in a fixed order
stoi = {ch: i for i, ch in enumerate(chars)}  # string -> integer
itos = {i: ch for i, ch in enumerate(chars)}  # integer -> string
encode = lambda s: [stoi[c] for c in s]
decode = lambda ids: "".join(itos[i] for i in ids)
```

For the full addition dataset the vocabulary is 13 characters: the ten digits, `+`, `=` and newline.

### Why sorted? Why save it?

The ids must be identical at training time and at inference time. If `"7"` is id 4 during
training but id 9 later, the model's weights will be read with the wrong meaning and output
garbage. So the vocabulary is sorted (deterministic order) and saved **inside the checkpoint**
alongside the weights (see `tinymodel/checkpoint.py`).

### What real models do differently

Large models use **subword** tokenizers such as **BPE** (byte-pair encoding). These give frequent
chunks like `"ing"` or `" the"` their own id, so text becomes far fewer tokens and the model can
see more of it at once. The vocabulary is 30,000 to 200,000 tokens instead of 13. The idea, a fixed
lookup table in each direction, is exactly the same. Characters are just easier to see and debug.

---

## Part 4: Inputs, targets and batches

**File:** `tinymodel/train.py`, function `get_batch`

### What exactly is the model asked to do?

The whole training set is turned into one long list of ids. We cut a random **window** out of it,
`block_size + 1` characters long, and split it into an input `x` and a target `y` that is the same
window shifted by one:

```
text  :  \n 2 3 + 5 8 = 8 1 \n 4 ...
x     :  \n 2 3 + 5 8 = 8          (what the model sees)
y     :   2 3 + 5 8 = 8 1          (what it should predict at each position)
```

One window gives many training examples at once: predict `2` after `\n`, predict `3` after `\n2`,
... predict `1` after `\n23+58=8`. The model makes a prediction at **every** position in parallel.

`block_size` is the **context length** (also called the context window): the maximum number of
previous characters the model can look at. We use 32, which is enough to see a couple of whole
problems. Claude and GPT have context lengths of hundreds of thousands of tokens.

### Why batches?

Instead of one window at a time, we take `batch_size` (64) random windows and stack them into a
`(B, T)` tensor. Two reasons:

1. **Speed.** Computers are much faster doing 64 similar calculations together than 64 separate
   ones.
2. **Smoother learning.** The gradient from one example is noisy. Averaging over 64 gives a more
   reliable direction to step in.

```python
def get_batch(data, block_size, batch_size, device):
    starts = torch.randint(len(data) - block_size - 1, (batch_size,))
    x = torch.stack([data[i : i + block_size] for i in starts])
    y = torch.stack([data[i + 1 : i + block_size + 1] for i in starts])
    return x.to(device), y.to(device)
```

The test `test_get_batch_targets_are_inputs_shifted_by_one` checks the shift is right.

---

## Part 5: A first model: the bigram

Before the transformer, let's build the simplest possible language model, so the core ideas
(embeddings, logits, softmax, loss) are clear before attention is added on top. This snippet is
self-contained; save it as `bigram.py` in `train-small-model/` and run `uv run python bigram.py`
after creating the dataset.

```python
import torch
import torch.nn.functional as F

text = open("data/addition/train.txt").read()
chars = sorted(set(text))
stoi = {c: i for i, c in enumerate(chars)}
data = torch.tensor([stoi[c] for c in text])
V = len(chars)

# The whole model: a V x V table. Row i holds a score for every possible next char after char i.
torch.manual_seed(0)
table = torch.zeros(V, V, requires_grad=True)
opt = torch.optim.AdamW([table], lr=0.1)

for step in range(500):
    ix = torch.randint(len(data) - 1, (256,))
    x, y = data[ix], data[ix + 1]  # current char, next char
    logits = table[x]  # (256, V) scores for the next char
    loss = F.cross_entropy(logits, y)  # how wrong were we?
    opt.zero_grad()
    loss.backward()
    opt.step()
    if step % 100 == 0:
        print(step, round(loss.item(), 3))

probs = F.softmax(table[stoi["="]], dim=0)
print({c: round(p.item(), 2) for c, p in zip(chars, probs) if p > 0.05})
```

You'll see the loss drop from about 2.56 to about 2.3 and then stall, and the model learns, for
example, that after `=` the next character is `1` about half the time (because about half the sums
are 100 or more). That is all a bigram can learn: it only ever looks at **one** previous
character, so it can't possibly know what `23+58=` adds up to. Keep that 2.3 in mind: the
transformer in Part 6 gets down to about 1.14 on the same data.

The new words in that snippet:

* **Embedding / lookup table.** `table[x]` looks up a row of numbers for each character id. In the
  transformer, `nn.Embedding` does the same, but the row is a vector of features (64 numbers)
  instead of directly a score per next character.
* **Logits.** Raw, unnormalised scores, one per vocabulary entry. Higher means "more likely".
  They can be any real number.
* **Softmax.** Turns logits into probabilities that are all positive and sum to 1:
  `softmax(z)_i = exp(z_i) / sum_j exp(z_j)`.
* **Cross-entropy loss.** `-log(probability the model gave to the correct next character)`. If the
  model gave the right answer probability 1, the loss is 0. If it gave it 0.01, the loss is 4.6.
  It punishes confident wrong answers heavily.

**A sanity check you should always do:** before training, a model that knows nothing gives every
character equal probability `1/V`, so the loss should be about `-log(1/V) = ln(V)`. With `V = 13`
that is `ln(13) ≈ 2.56`. Our transformer starts at about 2.80. If your starting loss is far above
`ln(V)`, something is wrong with the initialisation. `test_model_shapes_and_initial_loss_is_near_random_guessing`
checks this.

---

## Part 6: The transformer

**File:** `tinymodel/model.py`

To add numbers the model must look back at *several* earlier characters and combine them: to
produce the `1` in `23+58=81`, it needs the `3` and the `8`, and whether there was a carry. The
mechanism that lets each position gather information from earlier positions is **attention**.

### The full shape of the model

```
ids (B,T)
  │
  ├─ token embedding     "which character is this?"       ─┐
  ├─ position embedding  "where in the window is it?"      ─┴─ add ─► x (B,T,C)
  │
  ├─ Block 1:  x = x + Attention(LayerNorm(x))     communicate
  │            x = x + FeedForward(LayerNorm(x))   compute
  ├─ Block 2:  (same again, with its own weights)
  │
  ├─ LayerNorm
  └─ Linear "lm_head"  ─►  logits (B,T,V)   a score for every possible next character
```

### Step A: embeddings

`nn.Embedding(vocab_size, n_embd)` is a learnable table with one row of `n_embd` (64) numbers per
character. After training, these rows end up encoding useful features: digits that behave
similarly end up with similar vectors.

Attention by itself has no sense of order (it would treat `23` and `32` the same). So we add a
second table, the **position embedding**, with one learnable vector per position 0..31. Adding the
two gives each position a vector meaning "the character `3`, at position 5".

### Step B: self-attention, one head

For every position, the model computes three vectors from `x` using three small linear layers:

* **query**: "what am I looking for?"
* **key**: "what do I contain?"
* **value**: "what will I pass on if someone attends to me?"

The **attention score** between position `t` and earlier position `s` is the dot product
`query[t] · key[s]`: large when what `t` is looking for matches what `s` contains. Softmax turns
each row of scores into weights that sum to 1. The output at `t` is the weighted average of the
values.

```python
k = self.key(x)  # (B, T, hs)
q = self.query(x)  # (B, T, hs)
scores = q @ k.transpose(-2, -1) * hs**-0.5  # (B, T, T): every query vs every key
scores = scores.masked_fill(tril == 0, -inf)  # causal mask: no peeking at the future
weights = softmax(scores, dim=-1)  # each row sums to 1
out = weights @ self.value(x)  # (B, T, hs): weighted average of values
```

Three details:

1. **The causal mask.** During training the model sees the whole window at once, including the
   answer. Without a mask, position `t` could just look at position `t+1` and copy the answer.
   `tril` is a lower-triangular matrix; setting the scores above the diagonal to `-inf` makes
   their softmax weight exactly 0. `test_causal_mask_hides_the_future` proves that changing the
   last character doesn't change any earlier prediction.
2. **The `hs ** -0.5` scaling.** Dot products of long vectors get large; large scores make softmax
   extremely peaked and gradients vanish. Dividing by `sqrt(head_size)` keeps them in a sensible
   range.
3. **"Self"-attention** means queries, keys and values all come from the same sequence.

A tiny worked example. Suppose at position `=` the query strongly matches the keys of the two
units digits (`3` and `8`) and nothing else. Its weights might be
`[0.01, 0.02, 0.48, 0.01, 0.02, 0.46, ...]`, so its output is mostly the average of the values of
`3` and `8`. That is how the model can "fetch" the digits it needs.

### Step C: multi-head attention

One head can only learn one kind of "looking". `MultiHeadAttention` runs `n_head` (4) heads side by
side, each with `head_size = n_embd / n_head = 16`, concatenates their outputs back to 64 numbers
and mixes them with one more linear layer. One head might learn to find the units digits, another
the tens digits, another the `+` sign.

### Step D: the feed-forward network

After attention has gathered information, a small two-layer network processes each position on
its own: `Linear(64 → 256) → GELU → Linear(256 → 64)`. **GELU** is a **non-linearity** (an
activation function), a smooth version of "keep positive values, squash negatives". Without
non-linearities, any stack of linear layers collapses into a single linear layer, and the model
couldn't learn anything as bendy as "carry the one".

### Step E: residual connections and LayerNorm

```python
x = x + self.attn(self.ln1(x))
x = x + self.ffwd(self.ln2(x))
```

* **Residual connection** (`x + ...`): each sub-layer adds a correction to its input rather than
  replacing it. Gradients can flow straight through the `+` back to early layers, which makes deep
  stacks trainable.
* **LayerNorm**: rescales each position's vector to mean 0 and variance 1 (then applies a learned
  scale and shift). It keeps numbers in a stable range so training doesn't blow up.

Attention + feed-forward + residuals + LayerNorm = one **transformer block**. We stack `n_layer`
(2) of them. More blocks = more steps of "gather, then think".

### Step F: back to vocabulary scores

A final LayerNorm, then `lm_head = nn.Linear(64, vocab_size)` maps each position's vector to one
logit per character. Cross-entropy against the targets gives the loss, exactly as in the bigram.

### How big is it?

```
device=cpu  vocab=13  parameters=103,437
```

About 100k parameters. Most live in the blocks: each block has 4 × (64×64) for attention plus
2 × (64×256) for the feed-forward, roughly 50k.

---

## Part 7: The training loop

**File:** `tinymodel/train.py`

### The four lines that matter

```python
for step in range(max_steps):
    x, y = get_batch(train_data, block_size, batch_size, device)
    logits, loss = model(x, y)  # 1. forward pass
    optimizer.zero_grad()  # 2. clear old gradients
    loss.backward()  # 3. backward pass (backpropagation)
    optimizer.step()  # 4. update the weights
```

1. **Forward pass**: run the batch through the model and compute the loss.
2. **Zero the gradients**: PyTorch *adds* new gradients to old ones by default, so we clear them
   each step.
3. **Backward pass**: compute the gradient of the loss with respect to all 100k weights.
4. **Optimizer step**: move each weight a small step downhill.

### The optimizer: AdamW

The simplest optimizer, **SGD** (stochastic gradient descent), does
`weight = weight - learning_rate * gradient`. **AdamW** is a smarter version used for almost all
transformers: it keeps a running average of each weight's gradients (momentum, so it isn't thrown
around by noisy batches) and gives each weight its own effective step size. The "W" is weight
decay: a gentle pull of all weights toward zero, which discourages memorisation.

### Hyperparameters

**Hyperparameters** are settings *you* choose; the model doesn't learn them. They live in
`tinymodel/config.py` and can all be changed from the command line.

| Setting | Default | What it controls | Too small | Too big |
|---|---|---|---|---|
| `block_size` | 32 | Context length | Can't see the whole problem | Slower; more memory |
| `n_embd` | 64 | Width of each vector | Not enough capacity | Slower; may memorise |
| `n_head` | 4 | Attention heads | Fewer patterns at once | Each head gets tiny |
| `n_layer` | 2 | Number of blocks | Shallow reasoning | Slower; harder to train |
| `dropout` | 0.0 | Randomly zero activations during training | – | Learns slowly |
| `batch_size` | 64 | Windows per step | Noisy updates | Slower steps |
| `learning_rate` | 3e-3 | Step size | Takes forever | Loss explodes / jumps around |
| `max_steps` | 3000 | How long to train | Underfits | Overfits (on small data) |

A small model like ours tolerates a fairly high learning rate (3e-3). Big models use much smaller
ones (around 1e-4 to 6e-4) and usually change it over time: a short **warm-up** from 0, then a slow
decay. We keep it constant to stay simple.

### Run it

```bash
uv run python -m tinymodel.train --data data/addition
```

```
device=cpu  vocab=13  parameters=103,437
train tokens=74,348  val tokens=9,260
step     0 | train loss 2.7959 | val loss 2.8069 |   0.4s  <- saved (best val so far)
step   250 | train loss 1.5410 | val loss 1.5525 |   6.3s  <- saved (best val so far)
step   500 | train loss 1.4474 | val loss 1.4626 |  12.1s  <- saved (best val so far)
...
step  2500 | train loss 1.1408 | val loss 1.1536 |  57.3s  <- saved (best val so far)
step  2750 | train loss 1.1311 | val loss 1.1414 |  62.9s  <- saved (best val so far)
step  3000 | train loss 1.1229 | val loss 1.1393 |  68.3s  <- saved (best val so far)
best val loss 1.1393; checkpoint -> checkpoints/addition.pt; curve -> checkpoints/addition.history.json
```

(Your numbers will differ slightly by machine; the shape of the curve is what matters.)

### Reading the log

* **Step 0, loss ≈ 2.8**: close to `ln(13) = 2.56`, random guessing. Good sanity check.
* **Steps 0 to 250, fast drop**: the model learns the easy things: the format, that digits follow
  digits, that `+` and `=` appear once per line, that sums of two-digit numbers often start with 1.
* **Steps 250 to 3000, slow drop**: the hard part, actually learning to add.
* **Train and val stay close together**: the model isn't memorising; what it learns works on
  problems it has never seen. That is what we want.

### Why doesn't the loss go to zero?

Good question to ask about any loss curve. Look at a line: `48+95=143`. The `4`, `8`, `9` and `5`
are *random*; no model can predict them. Only the answer is predictable. A rough calculation:
picking `a` and `b` is about `ln(100) + ln(100) ≈ 9.2` nats of unavoidable surprise per line, and a
line is about 9.3 characters, so the best possible average loss is roughly **1.0**. We reached 1.14.
That is close to the floor, which means the predictable part (the answer) is almost perfect. Always
ask "what is the best loss possible on this data?" before deciding a number is good or bad.

### Why save the "best" checkpoint?

Every `eval_interval` steps we measure train and val loss averaged over 20 batches (one batch is
too noisy). Whenever val loss improves, we save a **checkpoint** (weights + config + tokenizer).
If training goes on too long and val loss starts rising, the saved checkpoint is still the best
one. This is called **early stopping**, and you'll see it matter in Part 10.

---

## Part 8: Evaluation

**File:** `tinymodel/evaluate.py`

Evaluation answers "how good is the model on data it has never seen?" We now use the **test** set,
which nothing so far has touched.

```bash
uv run python -m tinymodel.evaluate --checkpoint checkpoints/addition.pt --data data/addition
```

```
checkpoint from step 3000 (val loss during training 1.1393)
test loss 1.1308  perplexity 3.10  (vocab size 13)
  some mistakes:
    4+21= expected 25, model said '35'
    3+8= expected 11, model said '4'
    52+59= expected 111, model said '101'
    15+64= expected 79, model said '89'
    5+15= expected 20, model said '60'
    48+1= expected 49, model said '59'
    1+64= expected 65, model said '66'
    2+93= expected 95, model said '105'
exact-match accuracy on 500 unseen problems: 95.2%
```

### Metric 1: loss and perplexity

**Perplexity** = `exp(loss)`. It reads as "on average, the model is as unsure as if it were choosing
uniformly between this many characters". Random guessing on 13 characters = perplexity 13. Ours is
3.1, and remember most of that comes from the unpredictable question digits.

Loss and perplexity work for any dataset, which is why they are the default. But they are a
*proxy*. Nobody really cares about perplexity; they care whether the answers are right.

### Metric 2: task accuracy

For addition we can measure what we actually care about. For each test problem we give the model
`\n23+58=`, let it generate until a newline, and compare with the true answer. **Exact match**:
`81` is right, `80` is wrong, no partial credit.

We generate **greedily** (always pick the single most likely next character), because we want the
model's best answer, not a creative one.

**95.2% on 500 problems the model never saw during training.** It didn't memorise a table of
answers; it learned (most of) a procedure for adding.

### Metric 3: look at the mistakes

Numbers summarise; examples explain. **Error analysis** means reading the failures and looking for
a pattern. Here, nearly every mistake involves a **one-digit number** (`4+21`, `3+8`, `48+1`). Why?

* Only about 1 in 5 training problems contains a one-digit number, so the model has seen that
  pattern less often.
* For `4+21`, the units digit of the answer depends on the `4` and the `1`, which sit at different
  distances from the `=` than in the usual `ab+cd=` layout. The model has to learn a separate case.

That suggests concrete fixes, which is the point of error analysis: zero-pad numbers (`04+21=025`)
so every problem has the same layout, or generate more short problems, or train longer. See
[Part 11](#part-11-experiments-to-try).

### Underfitting and overfitting

Comparing train loss with val loss tells you which problem you have:

| Pattern | Name | Meaning | Fixes |
|---|---|---|---|
| Both high, both still falling | Under-trained | Not done yet | Train longer |
| Both high, both flat | **Underfitting** | Model too small or too simple | Bigger model, better features, higher LR |
| Train low, val rising | **Overfitting** | Memorising the training data | More data, dropout, smaller model, stop earlier |
| Both low, close together | Good fit | Learning patterns that generalise | Ship it (to the test set) |

Our addition run is the "good fit" row. Part 10 shows the overfitting row.

---

## Part 9: Inference

**File:** `tinymodel/generate.py`

**Inference** means using a trained model, with no more learning. The weights are frozen
(`model.eval()` and `torch.no_grad()` tell PyTorch not to track gradients or apply dropout).

### Generation, one character at a time

The model only ever predicts *one* next character. To produce text we loop:

```python
for _ in range(max_new_tokens):
    logits, _ = model(idx[:, -block_size:])  # only the last block_size chars fit
    logits = logits[:, -1, :]  # we only need the prediction for the last position
    probs = softmax(logits / temperature)
    next_id = torch.multinomial(probs, 1)  # sample, or argmax for greedy
    idx = torch.cat([idx, next_id], dim=1)  # append and go again
```

This is called **autoregressive** generation: each output becomes part of the next input. It is
exactly how ChatGPT and Claude produce text, just with subword tokens and much bigger models.

```bash
uv run python -m tinymodel.generate --prompt "41+37="
# 41+37=78

uv run python -m tinymodel.generate --max-new 60
# 77+87=164
# 17+26=43
# 37+56=93
# ...
```

The second command gives the model only a newline and lets it **sample**: it invents its own
problems (random-looking, just like the training data) and solves them.

### Greedy vs. sampling, and temperature

* **Greedy**: always take the most likely character. Deterministic. Best when there is one right
  answer (addition).
* **Sampling**: pick randomly according to the probabilities. Produces variety. Best for creative
  text.
* **Temperature** divides the logits before softmax. `T < 1` sharpens the distribution (safer, more
  repetitive); `T > 1` flattens it (more surprising, more mistakes). `T → 0` becomes greedy.

Try `--temperature 0.3` and `--temperature 1.5` on your own text model and compare.

---

## Part 10: Training on your own data

Now repeat the whole pipeline on text you choose. The repo includes a short sample,
`examples/my_text.txt` (a made-up garden journal of about 4,700 characters), so you can try it
before finding your own.

```bash
uv run python -m tinymodel.dataset text --input examples/my_text.txt --name my_text
uv run python -m tinymodel.train --data data/my_text --max-steps 1500 --dropout 0.2
```

```
train tokens=4,214  val tokens=469
step     0 | train loss 3.9732 | val loss 3.9741 |   0.4s  <- saved (best val so far)
step   250 | train loss 1.3917 | val loss 1.8378 |   8.8s  <- saved (best val so far)
step   500 | train loss 0.8595 | val loss 1.8739 |  16.8s
step   750 | train loss 0.6064 | val loss 2.0747 |  25.4s
step  1000 | train loss 0.4633 | val loss 2.2444 |  34.2s
step  1250 | train loss 0.3930 | val loss 2.4309 |  42.3s
step  1500 | train loss 0.3430 | val loss 2.5903 |  51.0s
best val loss 1.8378; checkpoint -> checkpoints/my_text.pt
```

### This is what overfitting looks like

After step 250, **train loss keeps falling but val loss rises**. The model is memorising the 4,214
training characters, and the more it memorises, the worse it does on the unseen ending. This is the
"overfitting" row of the table in Part 8, and it happens because 4,700 characters is tiny for a
100,000-parameter model: it has more knobs than there are characters to learn from.

Early stopping saved us: the checkpoint is from step 250, the best val loss, not step 1500.

```bash
uv run python -m tinymodel.evaluate --checkpoint checkpoints/my_text.pt --data data/my_text
# val loss 1.8239  perplexity 6.20  (vocab size 45)

uv run python -m tinymodel.generate --checkpoint checkpoints/my_text.pt \
    --prompt "The robin " --temperature 0.8 --max-new 250
```

```
The robin came the the peas denike coverme by the sleedlinges a the plant, puth the the the
seedlings sows glit and the shentill on the soichter gall rows the firdeng wand harm tome of ...
```

Garbled, but look at what it learned from under 5 KB of text: spaces between word-sized chunks,
real words from the journal (`peas`, `plant`, `seedlings`, `rows`), "the" everywhere, punctuation.
It is learning English spelling one character at a time.

### Making it better

In order of how much they help:

1. **More data.** By far the biggest lever. Try 1 MB or more of consistent text: your own notes,
   a public-domain book from Project Gutenberg, a code base, chat logs, product descriptions.
2. **Regularisation.** `--dropout 0.2` (already on above) randomly zeroes 20% of activations during
   training so the model can't rely on any single path; this slows memorisation.
3. **Right-size the model.** With little data, a smaller model (`--n-embd 32 --n-layer 1`)
   overfits less. With lots of data, go bigger (`--n-embd 128 --n-layer 4 --block-size 128`).
4. **Train for the right length.** Watch val loss; the best checkpoint is saved automatically.

A good rule of thumb from research on scaling: the number of training tokens should be at least
roughly 20 times the number of parameters. Our 100k-parameter model wants about 2 million
characters. The addition set (75k characters) works anyway because its pattern is very regular.

---

## Part 11: Experiments to try

Change one thing at a time, keep the seed fixed, and write down what happened. That habit is most
of what doing ML research looks like.

1. **Remove the causal mask** in `Head.forward` (comment out the `masked_fill` line). Training loss
   drops toward 0 suspiciously fast. Then try generating. Why does it fail? (Hint: Part 6, detail 1.)
2. **Learning rate sweep.** Train with `--lr 3e-2`, `--lr 3e-3`, `--lr 3e-4`. Which explodes, which
   crawls?
3. **Fewer layers.** `--n-layer 1`. Does addition accuracy drop? What about `--n-layer 4`?
4. **Shorter context.** `--block-size 4`. The model can't see the whole problem any more. What
   happens to accuracy, and why?
5. **Fix the one-digit weakness** found in Part 8. In `make_addition_problems`, zero-pad:
   `f"{a:02d}+{b:02d}={a + b:03d}"`. Retrain and compare accuracy.
6. **Reverse the answer** (`f"{a}+{b}={str(a + b)[::-1]}"`). Humans add from the right; the model
   writes left to right, so it must "think ahead" for carries. Reversing the answer lets it produce
   the units digit first. Does it learn faster?
7. **Generalisation test.** Generate a test set of three-digit problems. Does the model handle
   numbers bigger than any it saw? (Usually not, and that teaches a lot about what "learning" means
   here.)
8. **Plot the curve.** `checkpoints/addition.history.json` holds the loss history. Plot train and val
   loss against step with any tool you like.

---

## What you've built, and how it relates to the big models

| Our TinyGPT | A large production LLM |
|---|---|
| ~100 thousand parameters | Hundreds of billions |
| Character tokenizer, 13 to 45 tokens | Subword tokenizer, ~100k tokens |
| Context of 32 characters | Hundreds of thousands of tokens |
| 75 KB of data | Trillions of tokens |
| 1 minute on a laptop CPU | Months on thousands of GPUs |
| Same architecture: embeddings, attention, feed-forward, residuals, LayerNorm | Same, with refinements |
| Same objective: predict the next token | Same, followed by **fine-tuning** (more training on curated examples) and feedback-based training to make it helpful |

The core loop you wrote is the same.

### Where to go next

* Swap the character tokenizer for a BPE tokenizer (the `tiktoken` or `tokenizers` libraries).
* Add a learning-rate schedule (warm-up then cosine decay) and gradient clipping.
* Replace the hand-written attention with `torch.nn.functional.scaled_dot_product_attention`
  (much faster, same maths).
* **Fine-tuning**: instead of starting from random weights, start from a small pre-trained model
  (e.g. from Hugging Face) and continue training it on your data. With little data this beats
  training from scratch by a wide margin, because the model already knows the language.

---

## Glossary

* **Activation function / non-linearity**: a function like GELU or ReLU applied between linear
  layers so the network can learn non-linear patterns.
* **AdamW**: the standard optimizer for transformers; gradient descent with momentum, per-weight
  step sizes and weight decay.
* **Attention**: a mechanism that lets each position take a weighted average of information from
  other positions, with weights computed from query/key similarity.
* **Autoregressive**: generating one token at a time, feeding each output back in as input.
* **Backpropagation**: the algorithm (done by `loss.backward()`) that computes the gradient of the
  loss with respect to every weight.
* **Batch**: a group of examples processed together in one step.
* **Block size / context length**: the maximum number of tokens the model can look at.
* **Causal mask**: hides future positions so the model can't see what it is meant to predict.
* **Checkpoint**: a saved file with weights, config and tokenizer.
* **Cross-entropy**: the loss for classification; `-log(probability given to the right answer)`.
* **Data leakage**: test data accidentally appearing in training data, which inflates scores.
* **Dropout**: randomly zeroing activations during training to reduce overfitting.
* **Early stopping**: keeping the checkpoint with the best validation loss rather than the last one.
* **Embedding**: a learned vector that represents a token (or a position).
* **Epoch**: one full pass over the training data. (We count steps instead, since batches are
  random windows.)
* **Error analysis**: reading a model's mistakes to find patterns that suggest fixes.
* **Evaluation**: measuring a model on held-out data.
* **Fine-tuning**: continuing to train an already-trained model on new, usually smaller, data.
* **Forward pass**: running inputs through the model to get outputs and the loss.
* **Generalisation**: doing well on data not seen during training.
* **Gradient**: for each weight, how much the loss changes if that weight changes a little.
* **Greedy decoding**: always choosing the most likely next token.
* **Hyperparameter**: a setting chosen by you, not learned (learning rate, layers, etc.).
* **Inference**: using a trained model to make predictions.
* **LayerNorm**: normalises each vector to mean 0 / variance 1, keeping training stable.
* **Learning rate**: the size of each update step.
* **Logits**: raw scores before softmax.
* **Loss**: a single number measuring how wrong the model is; training minimises it.
* **Model**: a function with learnable parameters.
* **Optimizer**: the algorithm that uses gradients to update weights.
* **Overfitting**: memorising training data; train loss falls while validation loss rises.
* **Parameters / weights**: the numbers the model learns.
* **Perplexity**: `exp(loss)`; the effective number of choices the model is unsure between.
* **Residual connection**: `x + f(x)`; lets gradients flow through deep networks.
* **Seed**: a number that fixes randomness so runs are reproducible.
* **Softmax**: converts logits to probabilities that sum to 1.
* **Temperature**: scales logits before sampling; controls randomness.
* **Tensor**: a multi-dimensional array of numbers with a shape.
* **Test set**: held-out data used once at the end for the final score.
* **Token / tokenizer / vocabulary**: the units text is split into, the program that splits it, and
  the list of all possible units.
* **Training set**: the data the model learns from.
* **Transformer**: the architecture of embeddings + stacked attention/feed-forward blocks used here.
* **Underfitting**: the model is too weak to capture the patterns; both losses stay high.
* **Validation set**: held-out data checked during training to guide decisions.
