"""Deterministic compiler-semantic extraction from validated Clang JSON AST."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from .model import TranslationUnit, digest


def _children(node: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for child in node.get("inner", ()):
        if isinstance(child, Mapping):
            yield child


def _walk(node: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield node
    for child in _children(node):
        yield from _walk(child)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _is_main_function(node: Mapping[str, Any], unit: TranslationUnit) -> bool:
    if node.get("kind") != "FunctionDecl":
        return False
    location = node.get("loc")
    if not isinstance(location, Mapping) or "includedFrom" in location:
        return False
    file_value = location.get("file")
    if isinstance(file_value, str):
        path = Path(file_value)
        if not path.is_absolute():
            path = Path(unit.directory) / path
        return path.resolve() == Path(unit.source).resolve()
    return isinstance(location.get("offset"), int)


def _validate_consumed_shapes(ast: Mapping[str, Any], unit: TranslationUnit) -> None:
    for function in _walk(ast):
        if function.get("kind") == "FunctionDecl" \
                and any(child.get("kind") == "CompoundStmt"
                        for child in _children(function)):
            for field in ("id", "name", "loc", "range", "inner"):
                if field not in function:
                    raise ValueError(
                        f"FunctionDecl required field missing: {field}")
        if not _is_main_function(function, unit):
            continue
        for field in ("id", "name", "loc", "range"):
            if field not in function:
                raise ValueError(f"FunctionDecl required field missing: {field}")
        if not isinstance(function["id"], str) or not isinstance(function["name"], str):
            raise ValueError("FunctionDecl required identity fields have wrong type")
        for node in _walk(function):
            if node.get("kind") == "CallExpr":
                for field in ("id", "range", "inner"):
                    if field not in node:
                        raise ValueError(f"CallExpr required field missing: {field}")
            if node.get("kind") != "DeclRefExpr":
                continue
            referenced = node.get("referencedDecl")
            if not isinstance(referenced, Mapping) \
                    or referenced.get("kind") != "FunctionDecl":
                continue
            for field in ("id", "kind", "name"):
                if field not in referenced:
                    raise ValueError(
                        f"referencedDecl required field missing: {field}")


class _Locations:
    def __init__(self, source: Path, root: Path):
        self.source = source
        self.root = root
        self.raw = source.read_bytes()
        self.path = _relative(source, root)

    def point(self, raw: Mapping[str, Any]) -> tuple[int, int]:
        location = raw
        if isinstance(raw.get("expansionLoc"), Mapping):
            location = raw["expansionLoc"]
        offset = location.get("offset")
        line = location.get("line")
        column = location.get("col")
        if isinstance(line, int) and isinstance(column, int):
            return line, column
        if not isinstance(offset, int) or offset < 0 or offset > len(self.raw):
            raise ValueError("Clang location lacks a valid offset")
        prefix = self.raw[:offset]
        return prefix.count(b"\n") + 1, len(prefix.rsplit(b"\n", 1)[-1]) + 1

    def span(self, node: Mapping[str, Any], kind: str) -> dict[str, Any]:
        source_range = node.get("range")
        if not isinstance(source_range, Mapping):
            raise ValueError(f"{node.get('kind')} lacks range")
        begin = source_range.get("begin")
        end = source_range.get("end")
        if not isinstance(begin, Mapping) or not isinstance(end, Mapping):
            raise ValueError(f"{node.get('kind')} has malformed range")
        has_spelling = isinstance(begin.get("spellingLoc"), Mapping)
        has_expansion = isinstance(begin.get("expansionLoc"), Mapping)
        if has_spelling != has_expansion:
            raise ValueError("macro location requires spellingLoc and expansionLoc")
        end_spelling = isinstance(end.get("spellingLoc"), Mapping)
        end_expansion = isinstance(end.get("expansionLoc"), Mapping)
        if end_spelling != end_expansion:
            raise ValueError("macro location requires spellingLoc and expansionLoc")
        macro = has_spelling and has_expansion
        try:
            start_line, start_column = self.point(begin)
            end_line, end_column = self.point(end)
        except ValueError:
            location = node.get("loc")
            if isinstance(location, Mapping) \
                    and isinstance(location.get("line"), int):
                return {
                    "file": self.path,
                    "start_line": location["line"],
                    "start_column": location.get("col"),
                    "end_line": None,
                    "end_column": None,
                    "span_kind": kind,
                    "precision": "PARTIAL",
                }
            return {
                "file": self.path,
                "start_line": None,
                "start_column": None,
                "end_line": None,
                "end_column": None,
                "span_kind": kind,
                "precision": "UNKNOWN",
            }
        end_token = end.get("expansionLoc", end) if macro else end
        token_length = end_token.get("tokLen", 1)
        if isinstance(token_length, int) and token_length > 0:
            end_column += token_length - 1
        return {
            "file": self.path,
            "start_line": start_line,
            "start_column": start_column,
            "end_line": end_line,
            "end_column": end_column,
            "span_kind": "MACRO_EXPANSION" if macro else kind,
            "precision": "EXACT",
        }
    def macro_provenance(self, node: Mapping[str, Any]
                         ) -> dict[str, dict[str, int]] | None:
        source_range = node.get("range")
        if not isinstance(source_range, Mapping):
            return None
        begin = source_range.get("begin")
        if not isinstance(begin, Mapping):
            return None
        spelling = begin.get("spellingLoc")
        expansion = begin.get("expansionLoc")
        if not isinstance(spelling, Mapping) or not isinstance(expansion, Mapping):
            return None

        def value(location: Mapping[str, Any]) -> dict[str, int]:
            line, column = self.point(location)
            result = {"line": line, "column": column}
            if isinstance(location.get("offset"), int):
                result["offset"] = location["offset"]
            return result

        return {
            "spelling_location": value(spelling),
            "expansion_location": value(expansion),
        }


def _locations_for_node(node: Mapping[str, Any], unit: TranslationUnit,
                        root: Path, default: _Locations) -> _Locations:
    location = node.get("loc")
    if not isinstance(location, Mapping):
        return default
    raw_path = location.get("file")
    if not isinstance(raw_path, str):
        return default
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(unit.directory) / path
    path = path.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return default
    return _Locations(path, root)


def _entity(kind: str, name: str, qname: str, path: str,
            language: str = "c") -> dict[str, str]:
    local = digest({"kind": kind, "qname": qname, "path": path})[-24:]
    return {
        "local_id": f"clang:{kind.lower()}:{local}",
        "kind": kind,
        "name": name,
        "qualified_name": qname,
        "path": path,
        "language": language,
    }


def _fact(kind: str, subject: dict[str, str], predicate: str,
          obj: dict[str, Any], span: dict[str, Any], unit: TranslationUnit,
          *, modality: str = "UNKNOWN") -> dict[str, Any]:
    semantic = {
        "tu_identity": unit.tu_identity,
        "kind": kind,
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "source_span": span,
    }
    return {
        "provider_fact_id": "clang:" + digest(semantic).split(":", 1)[1],
        "kind": kind,
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "source_span": span,
        "claim": {
            "observation_basis": "DIRECT",
            "execution_modality": modality,
        },
    }


def _first_function_reference(node: Mapping[str, Any]
                             ) -> Mapping[str, Any] | None:
    for item in _walk(node):
        if item.get("kind") != "DeclRefExpr":
            continue
        referenced = item.get("referencedDecl")
        if isinstance(referenced, Mapping) \
                and referenced.get("kind") == "FunctionDecl" \
                and isinstance(referenced.get("id"), str):
            return referenced
    return None


def _first_decl_reference(node: Mapping[str, Any]
                         ) -> Mapping[str, Any] | None:
    for item in _walk(node):
        if item.get("kind") == "DeclRefExpr" \
                and isinstance(item.get("referencedDecl"), Mapping):
            return item["referencedDecl"]
    return None


def extract_facts(ast: Mapping[str, Any], unit: TranslationUnit, *,
                  repo_root: str | Path) -> tuple[dict[str, Any], ...]:
    root = Path(repo_root)
    source = Path(unit.source)
    locations = _Locations(source, root)
    _validate_consumed_shapes(ast, unit)
    all_declarations: dict[str, dict[str, Any]] = {}
    declarations: dict[str, dict[str, Any]] = {}
    current_declaration_file: Path | None = None
    for node in _walk(ast):
        loc = node.get("loc", {})
        raw_path = loc.get("file") if isinstance(loc, Mapping) else None
        explicit_path: Path | None = None
        if isinstance(raw_path, str):
            explicit_path = Path(raw_path)
            if not explicit_path.is_absolute():
                explicit_path = Path(unit.directory) / explicit_path
            explicit_path = explicit_path.resolve()
            current_declaration_file = explicit_path
        if node.get("kind") != "FunctionDecl" \
                or not isinstance(node.get("id"), str) \
                or not isinstance(node.get("name"), str):
            continue
        name = node.get("name")
        if not name:
            continue
        repo_owned = False
        decl_path: Path | None = None
        if explicit_path is not None:
            decl_path = explicit_path
        elif isinstance(loc, Mapping) and isinstance(loc.get("offset"), int) \
                and current_declaration_file is not None:
            # Clang JSON elides a repeated file field. Its location stream
            # retains the prior explicit file until the next file transition.
            decl_path = current_declaration_file
        elif _is_main_function(node, unit):
            decl_path = source.resolve()
        if decl_path is not None:
            path = _relative(decl_path, root)
            try:
                decl_path.resolve().relative_to(root.resolve())
                repo_owned = True
            except ValueError:
                pass
        else:
            path = locations.path
        qname = f"{path}::{name}" if node.get("storageClass") == "static" else name
        parameters: list[dict[str, Any]] = []
        for position, parameter in enumerate(
                child for child in _children(node)
                if child.get("kind") == "ParmVarDecl"):
            parameter_name = parameter.get("name") or f"arg{position}"
            parameters.append({
                "node": parameter,
                "entity": _entity(
                    "PARAMETER", parameter_name,
                    f"{qname}::param:{position}:{parameter_name}", path,
                    unit.language),
            })
        record = {
            "name": name,
            "qname": qname,
            "path": path,
            "entity": _entity("FUNCTION", name, qname, path, unit.language),
            "node": node,
            "parameters": parameters,
            "locations": (_Locations(decl_path, root)
                          if repo_owned and decl_path is not None else locations),
            "has_body": any(child.get("kind") == "CompoundStmt"
                            for child in _children(node)),
            "repo_owned": repo_owned,
        }
        all_declarations[node["id"]] = record
        if _is_main_function(node, unit):
            declarations[node["id"]] = record

    facts: list[dict[str, Any]] = []
    emitted_declarations: set[str] = set()
    for declaration in all_declarations.values():
        node = declaration["node"]
        if not declaration["repo_owned"]:
            continue
        if declaration["has_body"] and node["id"] not in declarations:
            continue
        fact_kind = "Function" if declaration["has_body"] else "Declaration"
        semantic_key = f"{fact_kind}:{declaration['entity']['qualified_name']}"
        if semantic_key in emitted_declarations:
            continue
        emitted_declarations.add(semantic_key)
        facts.append(_fact(
            fact_kind, declaration["entity"], "DECLARES",
            {"literal": {
                "kind": "FUNCTION", "name": declaration["name"],
                "linkage": "INTERNAL" if node.get("storageClass") == "static"
                else "EXTERNAL",
                "signature": node.get("type", {}).get("qualType"),
                "parameter_types": [
                    item["node"].get("type", {}).get("qualType")
                    for item in declaration["parameters"]
                ],
                "tu_identity": unit.tu_identity,
            }},
            declaration["locations"].span(node, "DECLARATION"), unit,
        ))

    def visit(node: Mapping[str, Any], caller: dict[str, Any] | None = None) -> None:
        if node.get("kind") == "FunctionDecl" and isinstance(node.get("id"), str):
            caller = declarations.get(node["id"], caller)
        if node.get("kind") == "CallExpr" and caller is not None:
            referenced = _first_function_reference(node)
            target = all_declarations.get(str(referenced.get("id"))) if referenced else None
            if target is not None:
                span = locations.span(node, "CALLSITE")
                facts.append(_fact(
                    "CALL", caller["entity"], "CALL",
                    {"entity": target["entity"]},
                    span, unit, modality="MUST",
                ))
                provenance = locations.macro_provenance(node)
                if provenance is not None:
                    facts.append(_fact(
                        "MacroExpansion", caller["entity"],
                        "GENERATED_BY_MACRO",
                        {"literal": {
                            **provenance,
                            "macro_name": None,
                            "name_precision": "UNKNOWN",
                        }},
                        {**span, "span_kind": "MACRO_PROVENANCE"}, unit,
                    ))
                arguments = list(_children(node))[1:]
                for position, (argument, formal) in enumerate(zip(
                        arguments, target["parameters"], strict=False)):
                    argument_span = locations.span(argument, "ARGUMENT")
                    actual_qname = (
                        f"{caller['qname']}::call:{span['start_line']}:"
                        f"{span['start_column']}::arg:{position}")
                    actual = _entity(
                        "EXPRESSION", f"arg{position}", actual_qname,
                        locations.path, unit.language)
                    facts.append(_fact(
                        "Binding", actual, "BINDS_TO",
                        {"entity": formal["entity"]}, argument_span, unit,
                        modality="MUST",
                    ))
            else:
                callee = next(iter(_children(node)), None)
                local_reference = _first_decl_reference(callee) if callee else None
                if local_reference is not None:
                    span = locations.span(node, "CALLSITE")
                    facts.append(_fact(
                        "IndirectCall", caller["entity"], "CALL",
                        {"none": True}, span, unit, modality="UNKNOWN",
                    ))
        if node.get("kind") == "DeclRefExpr" and caller is not None:
            referenced = node.get("referencedDecl")
            target = all_declarations.get(str(referenced.get("id"))) \
                if isinstance(referenced, Mapping) else None
            if target is not None:
                facts.append(_fact(
                    "Reference", caller["entity"], "REFERENCES",
                    {"entity": target["entity"]},
                    locations.span(node, "REFERENCE"), unit,
                ))
        for child in _children(node):
            visit(child, caller)

    visit(ast)
    return tuple(sorted(facts, key=lambda item: item["provider_fact_id"]))


def extract_include_facts(unit: TranslationUnit, dependencies: Iterable[Path], *,
                          repo_root: str | Path) -> tuple[dict[str, Any], ...]:
    root = Path(repo_root).resolve()
    source = Path(unit.source).resolve()
    source_path = _relative(source, root)
    subject = _entity("FILE", source.name, source_path, source_path, unit.language)
    span = {
        "file": source_path,
        "start_line": None,
        "start_column": None,
        "end_line": None,
        "end_column": None,
        "span_kind": "INCLUDE_DEPENDENCY",
        "precision": "UNKNOWN",
    }
    facts: list[dict[str, Any]] = []
    for dependency in sorted({Path(item).resolve() for item in dependencies}):
        if dependency == source:
            continue
        try:
            dependency_path = dependency.relative_to(root).as_posix()
        except ValueError:
            continue
        target = _entity(
            "FILE", dependency.name, dependency_path, dependency_path,
            "c++" if dependency.suffix.lower() in {".hpp", ".hh"} else "c")
        facts.append(_fact(
            "Include", subject, "INCLUDES", {"entity": target}, span, unit,
        ))
    return tuple(sorted(facts, key=lambda item: item["provider_fact_id"]))


__all__ = ["extract_facts", "extract_include_facts"]
