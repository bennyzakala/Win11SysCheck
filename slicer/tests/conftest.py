import os

# tests start the assistant from nothing, not from the bundled starting knowledge (subprocesses inherit it)
os.environ["UNISLICER_NO_SEED"] = "1"
