# Explanation of Benefits (EOB) Generator

Generates a member-facing EOB from a claim JSON file (Python 3.8+, standard library only).

## Quick start
```bash
python eob_generator.py sample                      # creates sample_claim.json
python eob_generator.py generate sample_claim.json  # prints and saves the EOB to eobs/
```

## Benefit order applied to each service line
1. Provider discount (billed -> allowed)
2. Not-covered check
3. Copay
4. Deductible
5. Coinsurance
6. Out-of-pocket maximum cap

Edit `sample_claim.json` (plan values, service lines) to test your own scenarios.
All data is synthetic.
