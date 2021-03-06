#!/usr/bin/env python
import argparse
import datetime
import os
import subprocess
import sys
from getpass import getpass

import bitbucket

try:
    from urllib.error import HTTPError, URLError
except ImportError:
    from urllib2 import HTTPError, URLError

try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote

try:
    input = raw_input
except NameError:
    pass

try:
    _range = xrange
except NameError:
    _range = range

_verbose = False
_quiet = False
_log = False
_logfile = "backuplog.txt"
_status = False
_statusfile = "last_status.txt"

class MaxBackupAttemptsReached(Exception):
    pass


def debug(message, output_no_verbose=False):
    """
    Outputs a message to stdout taking into account the options verbose/quiet.
    """
    global _quiet, _verbose, _log, _logfile
    if not _quiet and (output_no_verbose or _verbose):
        print("%s - %s" % (datetime.datetime.now(), message))
    if _log and (output_no_verbose or _verbose):
        with open(_logfile, 'a') as f:
            f.write("%s - %s\n" % (datetime.datetime.now(), message))


def exit(message, code=1):
    """
    Forces script termination using C based error codes.
    By default, it uses error 1 (EPERM - Operation not permitted)
    """
    global _quiet, _log, _logfile, _status, _statusfile
    if _status:
        with open(_statusfile, 'w') as f:
            f.write("FAILED %s" % datetime.datetime.now())
    if not _quiet and message and len(message) > 0:
        sys.stderr.write("%s (%s)\n" % (message, code))
    if _log and message and len(message) > 0:
        with open(_logfile, 'a') as f:
            f.write("%s (%s)\n" % (message, code))
    sys.exit(code)


def exec_cmd(command):
    """
    Executes an external command taking into account errors and logging.
    """
    global _verbose
    debug("Executing command: %s" % command)
    if not _verbose:
        if 'nt' == os.name:
            command = "%s > nul 2> nul" % command
        else:
            command = "%s > /dev/null 2>&1" % command
    resp = subprocess.call(command, shell=True)
    if resp != 0:
        exit("Command [%s] failed" % command, resp)


def compress(repo, location):
    """
    Creates a TAR.GZ file with all contents cloned by this script.
    """
    os.chdir(location)
    debug("Compressing repositories in [%s]..." % location, True)
    exec_cmd("tar -zcvf bitbucket-backup-%s-%s.tar.gz `ls -d *`" % (repo.get('owner'), datetime.datetime.now().strftime('%Y%m%d%H%m%s')))
    debug("Cleaning up...", True)
    for d in os.listdir(location):
        path = os.path.join(location, d)
        if os.path.isdir(path):
            exec_cmd("rm -rfv %s" % path)


def clone_repo(repo, backup_dir, http, username, password, mirror=False, with_wiki=False):
    global _quiet, _verbose
    scm = repo.get('scm')
    slug = repo.get('slug')
    owner = repo.get('owner')

    owner_url = quote(owner)
    if http and not all((username, password)):
        exit("Cannot backup via http without username and password" % scm)
    slug_url = quote(slug)
    command = None
    if scm == 'hg':
        if http:
            command = 'hg clone https://%s:%s@bitbucket.org/%s/%s' % (quote(username), quote(password), owner_url, slug_url)
        else:
            command = 'hg clone ssh://hg@bitbucket.org/%s/%s' % (owner_url, slug_url)
    if scm == 'git':
        git_command = 'git clone'
        if mirror:
            git_command = 'git clone --mirror'
        if http:
            command = "%s https://%s:%s@bitbucket.org/%s/%s.git" % (git_command, quote(username), quote(password), owner_url, slug_url)
        else:
            command = "%s git@bitbucket.org:%s/%s.git" % (git_command, owner_url, slug_url)
    if not command:
        exit("could not build command (scm [%s] not recognized?)" % scm)
    debug("Cloning %s..." % repo.get('name'))
    exec_cmd('%s "%s"' % (command, backup_dir))
    if with_wiki and repo.get('has_wiki'):
        debug("Cloning %s's Wiki..." % repo.get('name'))
        exec_cmd("%s/wiki %s_wiki" % (command, backup_dir))


def update_repo(repo, backup_dir, with_wiki=False, prune=False):
    scm = repo.get('scm')
    command = None
    os.chdir(backup_dir)
    if scm == 'hg':
        command = 'hg pull -u'
    if scm == 'git':
        command = 'git remote update'
        if prune:
            command = '%s %s' % (command, '--prune')
    if not command:
        exit("could not build command (scm [%s] not recognized?)" % scm)
    debug("Updating %s..." % repo.get('name'))
    exec_cmd(command)
    wiki_dir = "%s_wiki" % backup_dir
    if with_wiki and repo.get('has_wiki') and os.path.isdir(wiki_dir):
        os.chdir(wiki_dir)
        debug("Updating %s's Wiki..." % repo.get('name'))
        exec_cmd(command)


def main():
    parser = argparse.ArgumentParser(description="Usage: %prog [options] ")
    parser.add_argument("-u", "--username", dest="username", help="Bitbucket username")
    parser.add_argument("-p", "--password", dest="password", help="Bitbucket password")
    parser.add_argument("-k", "--oauth-key", dest="oauth_key", help="Bitbucket oauth key")
    parser.add_argument("-s", "--oauth-secret", dest="oauth_secret", help="Bitbucket oauth secret")
    parser.add_argument("-t", "--team", dest="team", help="Bitbucket team")
    parser.add_argument("-l", "--location", dest="location", help="Local backup location")
    parser.add_argument("-v", "--verbose", action='store_true', dest="verbose", help="Verbose output of all cloning commands")
    parser.add_argument("-q", "--quiet", action='store_true', dest="quiet", help="No output to stdout")
    parser.add_argument("-c", "--compress", action='store_true', dest="compress", help="Creates a compressed file with all cloned repositories (cleans up location directory)")
    parser.add_argument("-a", "--attempts", dest="attempts", type=int, default=1, help="max. number of attempts to backup repository")
    parser.add_argument('--mirror', action='store_true', help="Clone just bare repositories with git clone --mirror (git only)")
    parser.add_argument('--with-wiki', dest="with_wiki", action='store_true', help="Includes wiki")
    parser.add_argument('--http', action='store_true', help="Fetch via https instead of SSH")
    parser.add_argument('--skip-password', dest="skip_password", action='store_true', help="Ignores password prompting if no password is provided (for public repositories)")
    parser.add_argument('--prune', dest="prune", action='store_true', help="Prune repo on remote update")
    parser.add_argument('--ignore-repo-list', dest='ignore_repo_list', nargs='+', type=str, help="specify list of repo slug names to skip")
    parser.add_argument('--log', action='store_true', dest='log', help="log outputs to file 'backuplog.txt' in backup directory")
    parser.add_argument('--status', action='store_true', dest='status', help="output a 'SUCCESS' or 'FAILED' to file 'last_status.txt' in backup directory")
    args = parser.parse_args()
    location = args.location
    username = args.username
    password = args.password
    oauth_key = args.oauth_key
    oauth_secret = args.oauth_secret
    http = args.http
    max_attempts = args.attempts
    global _quiet
    _quiet = args.quiet
    global _verbose
    _verbose = args.verbose
    _mirror = args.mirror
    _with_wiki = args.with_wiki
    global _log
    _log = args.log
    global _status
    _status = args.status
    if _quiet:
        _verbose = False  # override in case both are selected

    if all((oauth_key, oauth_secret)):
        owner = args.team if args.team else username
    else:
        if not username:
            username = input('Enter bitbucket username: ')
        owner = args.team if args.team else username
        if not password:
            if not args.skip_password:
                password = getpass(prompt='Enter your bitbucket password: ')
    if not location:
        location = input('Enter local location to backup to: ')
    location = os.path.abspath(location)
    global _logfile
    _logfile = os.path.join(location, _logfile)
    global _statusfile
    _statusfile = os.path.join(location, _statusfile)

    # ok to proceed
    try:
        bb = bitbucket.BitBucket(
            username=username,
            password=password,
            oauth_key=oauth_key,
            oauth_secret=oauth_secret,
            verbose=_verbose,
        )
        user = bb.user(owner)
        repos = sorted(user.repositories(), key=lambda repo: repo.get("name"))
        if not repos:
            print("No repositories found. Are you sure you provided the correct password")
        for repo in repos:
            if args.ignore_repo_list and repo.get("slug") in args.ignore_repo_list:
                debug("ignoring repo %s with slug: %s" % (repo.get("name"), repo.get("slug")))
                continue

            debug("Backing up [%s]..." % repo.get("name"), True)
            backup_dir = os.path.join(location, repo.get("slug"))

            for attempt in range(1, max_attempts + 1):
                try:
                    if not os.path.isdir(backup_dir):
                        clone_repo(repo, backup_dir, http, username, password, mirror=_mirror, with_wiki=_with_wiki)
                    else:
                        debug("Repository [%s] already in place, just updating..." % repo.get("name"))
                        update_repo(repo, backup_dir, with_wiki=_with_wiki, prune=args.prune)
                except:
                    if attempt == max_attempts:
                        raise MaxBackupAttemptsReached("repo [%s] is reached maximum number [%d] of backup tries" % (repo.get("name"), attempt))
                    debug("Failed to backup repository [%s], keep trying, %d attempts remain" % (repo.get("name"), max_attempts - attempt))
                else:
                    break

        if args.compress:
            compress(repo, location)
        debug("Finished!", True)
        if _status:
            with open(_statusfile, 'w') as f:
                f.write("SUCCESS %s" % datetime.datetime.now())
    except HTTPError as err:
        if err.code == 401:
            exit("Unauthorized! Check your credentials and try again.", 22)  # EINVAL - Invalid argument
        else:
            exit("Connection Error! Bitbucket returned HTTP error [%s]." % err.code)
    except URLError as e:
        exit("Unable to reach Bitbucket: %s." % e.reason, 101)  # ENETUNREACH - Network is unreachable
    except (KeyboardInterrupt, SystemExit):
        exit("Operation cancelled. There might be inconsistent data in location directory.", 0)
    except MaxBackupAttemptsReached as e:
        exit("Unable to backup: %s" % e)
    except:
        if not _quiet:
            import traceback

            traceback.print_exc()
        exit("Unknown error.", 11)  # EAGAIN - Try again


if __name__ == '__main__':
    main()
