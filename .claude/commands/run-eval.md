Run the current affairs eval pipeline for today.

Go to /home/cal/current-affairs-eval and run the eval using /home/cal/.venv/evalproj/bin/python3.

Steps:
1. Run `python3 eval.py --model haiku` to scrape articles, generate questions, and evaluate with haiku
2. Run `python3 eval.py --model sonnet --eval-only` to evaluate with sonnet (reuse today's questions)
3. Run `python3 eval.py --model opus --eval-only` to evaluate with opus (reuse today's questions)
4. Show a summary table of scores across all three models from the results

If any step fails, report the error and continue with the next model where possible.
