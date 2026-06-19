.PHONY: demo calibrate selftest guardtest verify-refs run rescore clean

demo: calibrate selftest guardtest  ## no-API end-to-end proof (a stranger runs this)

calibrate:                        ## classifier vs ground-truth probes
	python3 dafny_eval.py calibrate

selftest:                         ## oracle: references verify, stripped versions fail
	python3 dafny_eval.py selftest

guardtest:                        ## Tier-0 guards catch assume / spec-drift / decreases-*
	python3 dafny_eval.py guardtest

verify-refs:                      ## sanity: every reference solution must verify
	@for f in solutions/*.dfy; do echo "== $$f =="; dafny verify --cores:1 $$f; done

run:                              ## example live run (needs ANTHROPIC_API_KEY etc.)
	python3 dafny_eval.py run --models oracle --k 1
	python3 dafny_eval.py report results/results.jsonl

clean:
	rm -f results/*.jsonl
	rm -rf __pycache__
