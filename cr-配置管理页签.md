\#实施如下修改

* 生成第三个页签，名称为“配置管理”。
* 此页签具有如下组件。
* 筛选框“课程”和下拉选项菜单，来源为PromptLeading表“SubjectID“字段的唯一值列表。
* 筛选框”用户“和下拉选项菜单，来源为PromptLeading表“UserID字段的唯一值列表。
* 两个筛选框可编辑，右边为搜索按钮，三项部件，高度相等，底部对齐。
* 可编辑的表格，字段名和来源设定如下：

  * 课程，表PromptLeading字段“SubjectID”
  * 提示词，表PromptLeading字段“Prompts”
  * 用户，表PromptLeading字段, 表PromptLeading字段“UserID"
  * 创建时间，表PromptLeading字段“Creation\_Time”
* 表格显示3行，具有滑动条，长宽可调节。
* 表格下方具有"保存"按钮，点击之后保存表格内容进入数据库表。
* 表格下方具有"新增"按钮，点击之后表格新增行，可编辑。
