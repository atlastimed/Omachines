"""Bounded, private operation summaries. Never store guest command output."""
def entries():
    from core import read_json,state_dir
    data=read_json(state_dir()/"history.json",[])
    return data if isinstance(data,list) else []


def append(value):
    from core import state_dir,write_json
    allowed=("requestId","machineId","action","status","message","updatedAt")
    item={k:value[k] for k in allowed if k in value}
    rows=entries()
    key=item.get("requestId")
    if key:
        rows=[r for r in rows if r.get("requestId")!=key]
    write_json(state_dir()/"history.json",(rows+[item])[-60:])
