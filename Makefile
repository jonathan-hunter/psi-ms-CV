ROBOT ?= java -jar robot.jar
# Pinned here rather than in the workflows so both the PR build check and the
# release build fetch the same jar, and so a toolchain change lands in a file that
# already requires a VERSION bump and triggers a rebuild.
ROBOT_VERSION := 1.9.10
VERSION := $(shell tr -d '[:space:]' < VERSION)
ONTOLOGY_IRI := http://purl.obolibrary.org/obo/ms.owl
VERSION_IRI := http://purl.obolibrary.org/obo/ms/$(VERSION)/ms.owl

.PHONY: release
release:
	$(ROBOT) merge \
		--input psi-ms-core.obo \
		--input psi-ms-columns.obo \
		--include-annotations false \
		annotate \
		--ontology-iri $(ONTOLOGY_IRI) \
		--version-iri $(VERSION_IRI) \
		--annotation owl:versionInfo $(VERSION) \
		--output psi-ms.owl
	$(ROBOT) convert \
		--input psi-ms.owl \
		--output psi-ms.obo
	perl -pi -e 's/[ \t]+$$//' psi-ms.owl psi-ms.obo
	perl -0pi -e 's/\n+\z/\n/' psi-ms.owl psi-ms.obo

# Assertions on the built release files. Run by the PR check against an ephemeral
# build and by the release workflow against the one it commits, so both hold the
# artefacts to the same contract.
.PHONY: verify
verify:
	grep -Fx "data-version: $(VERSION)" psi-ms.obo
	grep -F "$(VERSION_IRI)" psi-ms.owl
	! grep -F "components/psi-ms-" psi-ms.owl
	! grep -F "http://purl.obolibrary.org/obo/http://" psi-ms.owl
	python3 scripts/check_aggregate.py psi-ms.obo

.PHONY: print-robot-version
print-robot-version:
	@echo $(ROBOT_VERSION)
