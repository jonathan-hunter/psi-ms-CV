ROBOT ?= java -jar robot.jar
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
