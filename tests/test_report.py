def test_the_data_path_footer_names_the_command_of_its_own_source() -> None:
    from importer.core.report import data_path_section

    paths: list[str | None] = ["native", "needs-log-forwarding"]
    assert "plan-gcp-datapath" in "\n".join(data_path_section(paths, "gcp"))
    azure = "\n".join(data_path_section(paths, "azure"))
    assert "plan-gcp-datapath" not in azure and "azure-datapath.md" in azure
    other = "\n".join(data_path_section(paths, "grafana"))
    assert "plan-gcp-datapath" not in other and other.endswith("is sent.")
