PLEASEMAKE=fetchlatestrepo updatepackages

all: $(PLEASEMAKE)

fetchlatestrepo:
	rsync -aHx --verbose rsync://releases.merproject.org/mer-releases/obs-repos/latest.release obs-repos/latest.release
	rsync -aHx --verbose rsync://releases.merproject.org/mer-releases/obs-repos/Core:*:`cat obs-repos/latest.release` obs-repos
	rsync -aHx --verbose rsync://releases.merproject.org/mer-releases/obs-repos/Core:*:latest obs-repos

fetchnextrepo:
	rsync -aHx --verbose rsync://releases.merproject.org/mer-releases/obs-repos/next.release obs-repos/next.release
	rsync -aHx --verbose rsync://releases.merproject.org/mer-releases/obs-repos/Core:*:`cat obs-repos/next.release` obs-repos
	rm obs-repos/Core:*:next
	for nr in obs-repos/Core:*:`cat obs-repos/next.release`; do ln -s $$nr `dirname $$nr`/next

updatepackages:
	rsync -aHx --verbose --exclude=repos.lst --exclude=mappingscache.xml --exclude=.keep --delete-after rsync://releases.merproject.org/mer-releases/packages-git/ packages-git

update: fetchlatestrepo fetchnextrepo updatepackages
	
clean:
	rm -f $(PLEASEMAKE)
