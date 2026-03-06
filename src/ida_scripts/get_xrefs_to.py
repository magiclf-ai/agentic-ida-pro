import idautils
import idc

target_ea = int(__TARGET_EA__)
xrefs = []
for xref in idautils.XrefsTo(target_ea):
    xrefs.append(
        {
            "from": int(xref.frm),
            "to": int(xref.to),
            "type": idc.get_xref_type_name(xref.type),
        }
    )

__result__ = xrefs
