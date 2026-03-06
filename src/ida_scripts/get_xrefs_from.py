import idautils
import idc

source_ea = int(__SOURCE_EA__)
xrefs = []
for xref in idautils.XrefsFrom(source_ea):
    xrefs.append(
        {
            "from": int(xref.frm),
            "to": int(xref.to),
            "type": idc.get_xref_type_name(xref.type),
        }
    )

__result__ = xrefs
