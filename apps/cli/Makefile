OUTDIR := output
LIBDEVTANKROOT:=../..

EXAMPLE_TESTER_PY_MODS := \
    $(OUTDIR)/build/dt_db_base \
    $(OUTDIR)/build/dt_cli_base \
    $(OUTDIR)/build/example_lib \
    $(OUTDIR)/build/resources

default: $(OUTDIR)/bin/example_tester_cli.sh

$(OUTDIR)/bin/example_tester_cli.sh: $(OUTDIR)/bin/example_tester_cli.py Makefile
	mkdir -p $(OUTDIR)/bin
	$(LIBDEVTANKROOT)/create_run_script.sh $(OUTDIR)/bin/example_tester_cli.py $(OUTDIR)/bin/example_tester_cli.sh

$(OUTDIR)/bin/example_tester_cli.py: example_tester_cli.py $(EXAMPLE_TESTER_PY_MODS) Makefile
	mkdir -p $(OUTDIR)/bin
	cp example_tester_cli.py $(OUTDIR)/bin/example_tester_cli.py
	chmod +x $(OUTDIR)/bin/example_tester_cli.py

$(OUTDIR)/build/example_lib : example_lib/*.py Makefile $(OUTDIR)/lib/libbase.so
	mkdir -p $(OUTDIR)/lib/example_lib
	cp -v example_lib/*.py $(OUTDIR)/lib/example_lib/
	touch $(OUTDIR)/build/example_lib

$(OUTDIR)/build/resources: resources/* $(LIBDEVTANKROOT)/pylibapps/resources/* Makefile
	mkdir -p $(OUTDIR)/build
	mkdir -p $(OUTDIR)/resources
	cp -v resources/*   $(OUTDIR)/resources/
	cp -v $(LIBDEVTANKROOT)/pylibapps/resources/*  $(OUTDIR)/resources/
	touch $(OUTDIR)/build/resources

COVERAGERUN=PYTHONPATH=output/lib LD_LIBRARY_PATH=output/lib python3-coverage run --include "output/*" -a ./output/bin/example_tester_cli.py

define COVERAGE_TEST
  $(COVERAGERUN) --config $(1) update_tests tests/groups.yaml
  $(COVERAGERUN) --config $(1) update_tests tests/groups.yaml
  $(COVERAGERUN) --config $(1) list_groups
  $(COVERAGERUN) --config $(1) fake_dev 04
  $(COVERAGERUN) --config $(1) show_group 1
  $(COVERAGERUN) --config $(1) run_group "Sunny Day" 04
  $(COVERAGERUN) --config $(1) group_dump "Sunny Day"
  $(COVERAGERUN) --config $(1) run_group "Rainy Day" 04
  $(COVERAGERUN) --config $(1) group_dump "Rainy Day"
  $(COVERAGERUN) --config $(1) dev_results 04
  $(COVERAGERUN) --config $(1) dev_status 0
  $(COVERAGERUN) --config $(1) group_results "Sunny Day"
  $(COVERAGERUN) --config $(1) group_result "Sunny Day" 0
  $(COVERAGERUN) --config $(1) group_results 3
  $(COVERAGERUN) --config $(1) group_result 3 0
  $(COVERAGERUN) --config $(1) add_fail 04 "Sunny Day"
  $(COVERAGERUN) --config $(1) add_tar_filestore
  TESTER_ID=`$(COVERAGERUN) --config $(1) list_testers | awk '/[0-9]+ : [0-9a-zA-Z:]+ .*/{print $$1}'`; \
    echo "\'$${TESTER_ID}\'"; \
    $(COVERAGERUN) --config $(1) testers_results $${TESTER_ID}; \
    $(COVERAGERUN) --config $(1) testers_result $${TESTER_ID} 0
endef

coverage: default
	rm -rf /tmp/dt_test.db /tmp/dt_test_files
	python3-coverage erase
	echo "{ type: sqlite, filename: /tmp/dt_test.db, db_files: /tmp/dt_test_files }" > /tmp/dt_test.yaml
	$(call COVERAGE_TEST,/tmp/dt_test.yaml)
	python3-coverage combine
	python3-coverage html
	if [ "$$NOBROWSER" != "1" ]; then sensible-browser htmlcov/index.html; fi

coverage_mysql: default config_mysql_test.yaml
	mkdir -p /tmp/dtlib/dbfiles
	rm -rf /tmp/dtlib/dbfiles/*
	python3-coverage erase
	$(call COVERAGE_TEST,config_mysql_test.yaml)
	python3-coverage combine
	python3-coverage html
	if [ "$$NOBROWSER" != "1" ]; then sensible-browser htmlcov/index.html; fi

coverage_mysql_full: default config_mysql_test.yaml
	mkdir -p /tmp/dtlib/dbfiles
	rm -rf /tmp/dtlib/dbfiles/* /tmp/dt_test.db /tmp/dt_test_files
	python3-coverage erase
	echo "{ type: sqlite, filename: /tmp/dt_test.db, db_files: /tmp/dt_test_files }" > /tmp/dt_test.yaml
	$(call COVERAGE_TEST,/tmp/dt_test.yaml)
	$(call COVERAGE_TEST,config_mysql_test.yaml)
	python3-coverage combine
	python3-coverage html
	if [ "$$NOBROWSER" != "1" ]; then sensible-browser htmlcov/index.html; fi

include $(LIBDEVTANKROOT)/pylibapps/Makefile.pylibapps

include $(LIBDEVTANKROOT)/libbase/Makefile.libbase

include $(LIBDEVTANKROOT)/Makefile.post
