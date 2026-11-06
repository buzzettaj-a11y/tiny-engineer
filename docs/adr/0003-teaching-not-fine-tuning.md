# Chip learns by teaching, not fine-tuning

Joe wants Chip to learn what he needs. Chip keeps a memory file that both the local model and Claude read on every turn, and Joe adds to it by voice ("Chip, remember…", "Chip, forget…"). Fine-tuning the local model was rejected for now: it needs a strong GPU, hundreds of examples and a retrain for every change, and wouldn't carry over to Claude. Revisit only if teaching visibly falls short.
