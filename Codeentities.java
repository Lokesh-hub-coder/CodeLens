package com.codelensai.parser.model;

import java.util.ArrayList;
import java.util.List;

/**
 * Plain data holders for everything RepoParser extracts, serialized to JSON
 * by Jackson's field-based reflection (no getters/setters needed -- see
 * ParserApplication where the ObjectMapper is configured for field
 * visibility).
 *
 * Field names here are a deliberate contract with the Python side: they
 * must match the dataclass fields in backend/app/parsing/java_bridge.py
 * exactly (ClassInfo, MethodInfo, CallEdge, ImportEdge), because
 * java_bridge.py does `ClassInfo(**json_dict)` directly. If you rename a
 * field on either side, rename it on both.
 */
public final class CodeEntities {

    private CodeEntities() {
    }

    public static final class ClassInfo {
        public String id;            // fully-qualified name, e.g. "com.foo.Bar"
        public String file_path;     // relative to repo root, forward slashes
        public String name;

        // "package" is a reserved word in Java, so the field itself can't
        // be named that -- but java_bridge.py's ClassInfo dataclass expects
        // the JSON key "package" exactly. @JsonIgnore hides the field from
        // Jackson's field-based auto-detection so it does NOT also emit a
        // redundant "packageName" key; the getter below is then the *only*
        // source of this property, renamed via @JsonProperty. (Leaving the
        // field visible here would silently produce both "packageName" and
        // "package" in the output JSON, which would break the Python
        // dataclass constructor call with an unexpected-kwarg error.)
        @com.fasterxml.jackson.annotation.JsonIgnore
        public String packageName;

        public int start_line;
        public int end_line;
        public List<String> imports = new ArrayList<>();

        @com.fasterxml.jackson.annotation.JsonProperty("package")
        public String getPackage() {
            return packageName;
        }
    }

    public static final class MethodInfo {
        public String id;            // "com.foo.Bar#doThing(int,String)"
        public String class_id;
        public String name;
        public String signature;     // "doThing(int,String)"
        public String file_path;
        public int start_line;
        public int end_line;
        public List<String> modifiers = new ArrayList<>();
    }

    public static final class CallEdge {
        public String caller_id;
        public String callee_id;

        public CallEdge() {
        }

        public CallEdge(String callerId, String calleeId) {
            this.caller_id = callerId;
            this.callee_id = calleeId;
        }
    }

    public static final class ImportEdge {
        public String from_class_id;
        public String to_class_id;

        public ImportEdge() {
        }

        public ImportEdge(String fromClassId, String toClassId) {
            this.from_class_id = fromClassId;
            this.to_class_id = toClassId;
        }
    }

    /** Root object written to the output JSON file. */
    public static final class ParseResult {
        public List<ClassInfo> classes = new ArrayList<>();
        public List<MethodInfo> methods = new ArrayList<>();
        public List<CallEdge> call_edges = new ArrayList<>();
        public List<ImportEdge> import_edges = new ArrayList<>();
        public List<String> unparsed_files = new ArrayList<>();
    }
}