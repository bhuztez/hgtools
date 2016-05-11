import os.path
from mercurial import commands, error, extensions, hg, util, wireproto
from mercurial.i18n import _


def capabilities(orig, repo, proto):
    caps = orig(repo, proto)
    caps.append('remotemq')
    return caps


def has_queue(repo, name):
    if name != 'patches':
        name = name[8:]

    try:
        fh = repo.opener('patches.queues', 'r')
        queues = [queue.strip() for queue in fh if queue.strip()]
        fh.close()
    except IOError:
        return False

    return name in queues


def create_queue(repo, qname):
    if not has_queue(repo, qname):
        name = qname[8:] if qname != 'patches' else 'patches'
        fh = repo.opener('patches.queues', 'a')
        fh.write('%s\n' % (name,))
        fh.close()

    path = os.path.join(repo.path, qname)
    if not os.path.exists(path):
        hg.repository(repo.ui, path, create=True)


@wireproto.wireprotocommand('has_queue', 'name')
def wire_has_queue(repo, proto, name):
    return 'True' if has_queue(repo, name) else 'False'


@wireproto.wireprotocommand('create_queue', 'name')
def wire_create_queue(repo, proto, name):
    create_queue(repo, name)
    return 'OK'


def find_push_peer(repo, opts, dest):
    dest = repo.ui.expandpath(dest or 'default-push', dest or 'default')
    dest, branches = hg.parseurl(dest, opts.get('branch'))

    try:
        return hg.peer(repo, opts, dest)
    except error.RepoError:
        if dest == "default-push":
            return
        else:
            raise


def push(orig, ui, repo, dest=None, **opts):
    mq = opts.pop('mq', None)
    if not mq:
        return orig(ui, repo, dest, **opts)

    r = repo.mq.qrepo()
    if not r:
        raise util.Abort(_('no queue repository'))

    peer = find_push_peer(r, opts, dest)
    if peer is not None:
        return orig(r.ui, r, dest, **opts)

    qname = os.path.basename(repo.mq.path)
    peer = find_push_peer(repo, opts, dest)

    if peer.local():
        create_queue(peer._repo, qname)
        path = peer._repo.path + "/" + qname
    else:
        if not peer.capable('remotemq'):
            raise util.Abort(_("default repository not configured!"),
                    hint=_('see the "path" section in "hg help config"'))

        peer._call('create_queue', name=qname)
        path = peer.path
        if not path.endswith("/"):
            path += "/"
        path += ".hg/" + qname

    return orig(r.ui, r, path, **opts)


def find_pull_peer(repo, opts, source):
    source, branches = hg.parseurl(repo.ui.expandpath(source), opts.get('branch'))
    try:
        return hg.peer(repo, opts, source)
    except error.RepoError:
        if source == "default":
            return
        else:
            raise


def pull(orig, ui, repo, source="default", **opts):
    mq = opts.pop('mq', None)
    if not mq:
        return orig(ui, repo, dest, **opts)

    r = repo.mq.qrepo()
    if not r:
        raise util.Abort(_('no queue repository'))

    peer = find_pull_peer(r, opts, source)
    if peer is not None:
        return orig(r.ui, r, source, **opts)

    qname = os.path.basename(repo.mq.path)
    peer = find_pull_peer(repo, opts, source)

    if peer.local():
        if not has_queue(repo, qname):
            raise util.Abort(_("repository default not found!"))
        path = peer._repo.path + "/" + qname
    else:
        if not peer.capable('remotemq'):
            raise util.Abort(_("repository default not found!"))

        result = peer._call('has_queue', name=qname)
        if result != 'True':
            raise util.Abort(_("repository default not found!"))

        path = peer.path
        if not path.endswith("/"):
            path += "/"
        path += ".hg/" + qname

    return orig(r.ui, r, path, **opts)


def extsetup(ui):
    extensions.wrapfunction(wireproto, '_capabilities', capabilities)
    extensions.wrapcommand(commands.table, 'push', push)
    extensions.wrapcommand(commands.table, 'pull', pull)
