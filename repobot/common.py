import persistent.list
import persistent.mapping


def plist():
    return persistent.list.PersistentList()


def pmap():
    return persistent.mapping.PersistentMapping()
