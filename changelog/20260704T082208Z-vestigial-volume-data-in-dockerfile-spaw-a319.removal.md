Remove `VOLUME /data` directive from the Dockerfile. The app writes
under ``/home/mailbot`` and the anonymous volume created by this
directive accumulated orphan volumes on every container recreate.
